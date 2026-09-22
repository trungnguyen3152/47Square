// AI status light firmware for XIAO ESP32-C3.
// Wiring: D1/GPIO3=red, D2/GPIO4=yellow, D3/GPIO5=green,
// D4/GPIO6=active buzzer.

#include <esp_system.h>
#include <esp_ota_ops.h>
#include <mbedtls/md.h>
#include <mbedtls/sha256.h>
#include <Preferences.h>
#include <Update.h>
#include "security_config.h"
#include "firmware_version.h"

constexpr uint8_t RED_PIN = 3;
constexpr uint8_t YELLOW_PIN = 4;
constexpr uint8_t GREEN_PIN = 5;
constexpr uint8_t BUZZER_PIN = 6;
constexpr bool ACTIVE_HIGH = true;

String command;
String authNonce;
String deviceAuthKey;
uint32_t authNonceIssuedAt = 0;
constexpr uint32_t AUTH_NONCE_LIFETIME_MS = 10000;
Preferences preferences;

bool firmwareUpdateActive = false;
size_t firmwareBytesRemaining = 0;
size_t firmwareChunkBytesRemaining = 0;
uint8_t firmwareExpectedSha256[32];
mbedtls_sha256_context firmwareSha256;
uint32_t firmwareLastByteAt = 0;
constexpr uint32_t FIRMWARE_RECEIVE_TIMEOUT_MS = 30000;
constexpr size_t FIRMWARE_CHUNK_SIZE = 256;
String firmwareTargetVersion;

enum DecorEffect : uint8_t {
  DECOR_NONE, DECOR_STATIC, DECOR_BLINK, DECOR_BREATHING,
  DECOR_FADE_IN, DECOR_FADE_OUT, DECOR_SMOOTH, DECOR_CYCLE,
  DECOR_RAINBOW, DECOR_THEATER, DECOR_COMET, DECOR_METEOR,
  DECOR_SCANNER, DECOR_SPARKLE, DECOR_POLICE
};

#ifdef CONFIG_APP_ROLLBACK_ENABLE
// Arduino otherwise validates a pending OTA image inside initArduino(), before
// setup() can run our device checks or the intentional rollback diagnostic.
extern "C" bool verifyRollbackLater() {
  return true;
}
#endif

constexpr uint8_t LED_PINS[] = {RED_PIN, YELLOW_PIN, GREEN_PIN};
DecorEffect decorEffect = DECOR_NONE;
bool decorActive = false;
uint8_t decorMask = 7;
uint8_t decorSpeed = 50;
uint32_t decorLastUpdate = 0;
int16_t decorStep = 0;
int8_t decorDirection = 1;

void writeOutput(uint8_t pin, bool enabled) {
  digitalWrite(pin, (enabled == ACTIVE_HIGH) ? HIGH : LOW);
}

void writeLevel(uint8_t pin, uint8_t level) {
  analogWrite(pin, ACTIVE_HIGH ? level : 255 - level);
}

void setLevels(uint8_t red, uint8_t yellow, uint8_t green) {
  const uint8_t values[] = {red, yellow, green};
  for (uint8_t index = 0; index < 3; ++index) {
    writeLevel(LED_PINS[index], (decorMask & (1 << index)) ? values[index] : 0);
  }
}

void setLights(bool red, bool yellow, bool green) {
  writeLevel(RED_PIN, red ? 255 : 0);
  writeLevel(YELLOW_PIN, yellow ? 255 : 0);
  writeLevel(GREEN_PIN, green ? 255 : 0);
}

char hexDigit(uint8_t value) {
  return value < 10 ? static_cast<char>('0' + value) : static_cast<char>('a' + value - 10);
}

int8_t hexValue(char value) {
  if (value >= '0' && value <= '9') return value - '0';
  if (value >= 'a' && value <= 'f') return value - 'a' + 10;
  if (value >= 'A' && value <= 'F') return value - 'A' + 10;
  return -1;
}

bool decodeHex(const String &hex, uint8_t *output, size_t outputLength) {
  if (hex.length() != outputLength * 2) return false;
  for (size_t index = 0; index < outputLength; ++index) {
    const int8_t high = hexValue(hex[index * 2]);
    const int8_t low = hexValue(hex[index * 2 + 1]);
    if (high < 0 || low < 0) return false;
    output[index] = static_cast<uint8_t>((high << 4) | low);
  }
  return true;
}

void issueChallenge() {
  uint8_t nonceBytes[16];
  esp_fill_random(nonceBytes, sizeof(nonceBytes));
  authNonce = "";
  authNonce.reserve(32);
  for (uint8_t value : nonceBytes) {
    authNonce += hexDigit(value >> 4);
    authNonce += hexDigit(value & 0x0f);
  }
  authNonceIssuedAt = millis();
  Serial.print("NONCE ");
  Serial.println(authNonce);
}

bool authenticateCommand(const String &value, String &plainCommand) {
  if (!value.startsWith("AUTH ") || authNonce.isEmpty()) return false;
  if (millis() - authNonceIssuedAt > AUTH_NONCE_LIFETIME_MS) {
    authNonce = "";
    return false;
  }
  const int separator = value.indexOf(' ', 5);
  if (separator < 0) return false;
  const String suppliedMac = value.substring(5, separator);
  plainCommand = value.substring(separator + 1);

  uint8_t key[32];
  uint8_t supplied[32];
  uint8_t expected[32];
  const String signedValue = authNonce + "|" + plainCommand;
  authNonce = "";  // Every challenge is single-use, including failed attempts.
  if (!decodeHex(deviceAuthKey, key, sizeof(key)) || !decodeHex(suppliedMac, supplied, sizeof(supplied))) {
    return false;
  }
  const mbedtls_md_info_t *info = mbedtls_md_info_from_type(MBEDTLS_MD_SHA256);
  if (info == nullptr || mbedtls_md_hmac(
      info, key, sizeof(key),
      reinterpret_cast<const unsigned char *>(signedValue.c_str()), signedValue.length(), expected
    ) != 0) {
    return false;
  }
  uint8_t difference = 0;
  for (size_t index = 0; index < sizeof(expected); ++index) difference |= expected[index] ^ supplied[index];
  return difference == 0;
}

void beginFirmwareUpdate(const String &value) {
  const int sizeEnd = value.indexOf(' ', 13);
  if (sizeEnd < 0) {
    Serial.println("UPDATE ERROR FORMAT");
    return;
  }
  const size_t updateSize = static_cast<size_t>(value.substring(13, sizeEnd).toInt());
  const int shaEnd = value.indexOf(' ', sizeEnd + 1);
  const String expectedSha = shaEnd < 0
    ? value.substring(sizeEnd + 1)
    : value.substring(sizeEnd + 1, shaEnd);
  if (shaEnd < 0) {
    preferences.begin("ai-status", true);
    firmwareTargetVersion = preferences.getString("ota-target", "unknown");
    preferences.end();
  } else {
    firmwareTargetVersion = value.substring(shaEnd + 1);
  }
  firmwareTargetVersion.trim();
  if (updateSize == 0 || updateSize > 0x140000 ||
      !decodeHex(expectedSha, firmwareExpectedSha256, sizeof(firmwareExpectedSha256))) {
    Serial.println("UPDATE ERROR FORMAT");
    return;
  }
  if (!Update.begin(updateSize, U_FLASH)) {
    Serial.println("UPDATE ERROR BEGIN");
    return;
  }
  mbedtls_sha256_init(&firmwareSha256);
  if (mbedtls_sha256_starts(&firmwareSha256, 0) != 0) {
    mbedtls_sha256_free(&firmwareSha256);
    Update.abort();
    Serial.println("UPDATE ERROR HASH");
    return;
  }
  firmwareBytesRemaining = updateSize;
  firmwareChunkBytesRemaining = min(FIRMWARE_CHUNK_SIZE, firmwareBytesRemaining);
  firmwareUpdateActive = true;
  firmwareLastByteAt = millis();
  preferences.begin("ai-status", false);
  const esp_partition_t *sourcePartition = esp_ota_get_running_partition();
  preferences.putString("ota-previous", FIRMWARE_VERSION);
  preferences.putString("ota-target", firmwareTargetVersion);
  preferences.putString("ota-result", "started");
  preferences.putString("ota-source", sourcePartition == nullptr ? "" : sourcePartition->label);
  preferences.end();
  decorActive = false;
  setLights(false, true, false);
  Serial.println("READY");
}

void saveFirmwareUpdateResult(const char *result) {
  preferences.begin("ai-status", false);
  preferences.putString("ota-result", result);
  preferences.end();
}

void abortFirmwareUpdate(const char *message, const char *result) {
  if (firmwareUpdateActive) {
    mbedtls_sha256_free(&firmwareSha256);
  }
  firmwareUpdateActive = false;
  firmwareBytesRemaining = 0;
  firmwareChunkBytesRemaining = 0;
  Update.abort();
  saveFirmwareUpdateResult(result);
  setLights(true, false, false);
  Serial.println(message);
}

void finishFirmwareUpdate() {
  uint8_t actualSha256[32];
  const bool hashFinished = mbedtls_sha256_finish(&firmwareSha256, actualSha256) == 0;
  mbedtls_sha256_free(&firmwareSha256);
  uint8_t difference = 0;
  for (size_t index = 0; index < sizeof(actualSha256); ++index) {
    difference |= actualSha256[index] ^ firmwareExpectedSha256[index];
  }
  firmwareUpdateActive = false;
  if (!hashFinished || difference != 0) {
    Update.abort();
    saveFirmwareUpdateResult("sha256-error");
    setLights(true, false, false);
    Serial.println("UPDATE ERROR SHA256");
    return;
  }
  if (!Update.end()) {
    saveFirmwareUpdateResult("write-error");
    setLights(true, false, false);
    Serial.println("UPDATE ERROR WRITE");
    return;
  }
  setLights(false, false, true);
  Serial.println("UPDATE OK");
  Serial.flush();
  delay(250);
  ESP.restart();
}

void receiveFirmwareBytes() {
  uint8_t buffer[1024];
  while (firmwareUpdateActive && firmwareChunkBytesRemaining > 0 && Serial.available() > 0) {
    const size_t requested = min(sizeof(buffer), firmwareChunkBytesRemaining);
    const size_t received = Serial.readBytes(buffer, min(requested, static_cast<size_t>(Serial.available())));
    if (received == 0) return;
    if (Update.write(buffer, received) != received ||
        mbedtls_sha256_update(&firmwareSha256, buffer, received) != 0) {
      abortFirmwareUpdate("UPDATE ERROR WRITE", "write-error");
      return;
    }
    firmwareBytesRemaining -= received;
    firmwareChunkBytesRemaining -= received;
    firmwareLastByteAt = millis();
  }
  if (!firmwareUpdateActive || firmwareChunkBytesRemaining > 0) return;
  if (firmwareBytesRemaining == 0) {
    finishFirmwareUpdate();
    return;
  }
  firmwareChunkBytesRemaining = min(FIRMWARE_CHUNK_SIZE, firmwareBytesRemaining);
  Serial.println("ACK");
}

void confirmOrRollbackFirmware() {
  const esp_partition_t *running = esp_ota_get_running_partition();
  esp_ota_img_states_t state;
  preferences.begin("ai-status", false);
  const String target = preferences.getString("ota-target", "");
  const String previous = preferences.getString("ota-previous", "");
  const String sourceLabel = preferences.getString("ota-source", "");
  const String otaResult = preferences.getString("ota-result", "none");
  const bool forceRollback = preferences.getBool("ota-force", false);
  preferences.end();

  // Arduino's prebuilt bootloader variants do not all enable native rollback.
  // Keep a verified application-level fallback by returning to the exact
  // partition that initiated the update.
  if (forceRollback && otaResult == "started" &&
      !sourceLabel.isEmpty() && sourceLabel != running->label) {
    const esp_partition_t *source = esp_partition_find_first(
      ESP_PARTITION_TYPE_APP, ESP_PARTITION_SUBTYPE_ANY, sourceLabel.c_str()
    );
    if (source != nullptr && esp_ota_set_boot_partition(source) == ESP_OK) {
      preferences.begin("ai-status", false);
      preferences.putString("ota-result", "rollback-requested");
      preferences.putBool("ota-force", false);
      preferences.end();
      ESP.restart();
      return;
    }
  }

  if (esp_ota_get_state_partition(running, &state) == ESP_OK &&
      state == ESP_OTA_IMG_PENDING_VERIFY) {
    if (forceRollback) {
      preferences.begin("ai-status", false);
      preferences.putString("ota-result", "rollback-requested");
      preferences.putBool("ota-force", false);
      preferences.end();
      esp_ota_mark_app_invalid_rollback_and_reboot();
      return;
    }
    if (esp_ota_mark_app_valid_cancel_rollback() == ESP_OK) {
      preferences.begin("ai-status", false);
      if (otaResult != "rollback-requested") {
        preferences.putString("ota-result", "success");
      }
      preferences.putBool("ota-force", false);
      preferences.end();
    }
    return;
  }

  if (!target.isEmpty() && target != "unknown" && target != FIRMWARE_VERSION &&
      !previous.isEmpty() && previous == FIRMWARE_VERSION) {
    preferences.begin("ai-status", false);
    preferences.putString("ota-result", "rollback");
    preferences.putBool("ota-force", false);
    preferences.end();
  }
}

void printFirmwareUpdateStatus() {
  preferences.begin("ai-status", true);
  const String result = preferences.getString("ota-result", "none");
  const String target = preferences.getString("ota-target", "none");
  preferences.end();
  Serial.printf("UPDATE_STATUS %s %s\n", result.c_str(), target.c_str());
}

void printFirmwareDebug() {
  preferences.begin("ai-status", true);
  const String result = preferences.getString("ota-result", "none");
  const String source = preferences.getString("ota-source", "none");
  const bool forceRollback = preferences.getBool("ota-force", false);
  preferences.end();
  const esp_partition_t *running = esp_ota_get_running_partition();
  esp_ota_img_states_t state = ESP_OTA_IMG_UNDEFINED;
  esp_ota_get_state_partition(running, &state);
  Serial.printf(
    "OTA_DEBUG %s %s %s %u %u\n",
    running == nullptr ? "none" : running->label,
    source.c_str(), result.c_str(), forceRollback ? 1 : 0,
    static_cast<unsigned int>(state)
  );
}

void beepOnce() {
  // This pulse supports an active buzzer and remains audible on a passive one.
  writeOutput(BUZZER_PIN, true);
  tone(BUZZER_PIN, 2400, 180);
  delay(190);
  noTone(BUZZER_PIN);
  writeOutput(BUZZER_PIN, false);
}

void beep(uint8_t count) {
  for (uint8_t index = 0; index < count; ++index) {
    beepOnce();
    if (index + 1 < count) {
      delay(110);
    }
  }
}

uint16_t effectInterval(uint16_t slowMs, uint16_t fastMs) {
  return static_cast<uint16_t>(map(decorSpeed, 1, 100, slowMs, fastMs));
}

DecorEffect parseEffect(String effect) {
  if (effect == "STATIC") return DECOR_STATIC;
  if (effect == "BLINK") return DECOR_BLINK;
  if (effect == "BREATHING") return DECOR_BREATHING;
  if (effect == "FADE_IN") return DECOR_FADE_IN;
  if (effect == "FADE_OUT") return DECOR_FADE_OUT;
  if (effect == "SMOOTH") return DECOR_SMOOTH;
  if (effect == "CYCLE") return DECOR_CYCLE;
  if (effect == "RAINBOW") return DECOR_RAINBOW;
  if (effect == "THEATER") return DECOR_THEATER;
  if (effect == "COMET") return DECOR_COMET;
  if (effect == "METEOR") return DECOR_METEOR;
  if (effect == "SCANNER") return DECOR_SCANNER;
  if (effect == "SPARKLE") return DECOR_SPARKLE;
  if (effect == "POLICE") return DECOR_POLICE;
  return DECOR_NONE;
}

void stopDecor() {
  decorActive = false;
  decorEffect = DECOR_NONE;
  setLights(false, false, false);
}

void startDecor(String effectName, uint8_t mask, uint8_t speed) {
  DecorEffect parsed = parseEffect(effectName);
  if (parsed == DECOR_NONE) return;
  decorEffect = parsed;
  decorMask = mask & 7;
  decorSpeed = constrain(speed, 1, 100);
  decorStep = (decorEffect == DECOR_FADE_OUT) ? 255 : 0;
  decorDirection = 1;
  decorLastUpdate = 0;
  decorActive = true;
  noTone(BUZZER_PIN);
  writeOutput(BUZZER_PIN, false);
  if (decorEffect == DECOR_STATIC) setLevels(255, 255, 255);
  Serial.println("OK");
}

void smoothFrame(int16_t phase, bool rainbow) {
  const uint8_t from = (phase / 256) % 3;
  const uint8_t to = (from + 1) % 3;
  const uint8_t blend = phase % 256;
  uint8_t levels[] = {0, 0, 0};
  levels[from] = 255 - blend;
  levels[to] = blend;
  if (rainbow) levels[(to + 1) % 3] = min(levels[from], levels[to]) / 3;
  setLevels(levels[0], levels[1], levels[2]);
}

void updateDecor() {
  if (!decorActive || decorEffect == DECOR_STATIC) return;
  const uint32_t now = millis();
  uint16_t interval = effectInterval(700, 80);
  if (decorEffect == DECOR_BREATHING || decorEffect == DECOR_FADE_IN ||
      decorEffect == DECOR_FADE_OUT || decorEffect == DECOR_SMOOTH ||
      decorEffect == DECOR_RAINBOW) {
    interval = effectInterval(35, 4);
  } else if (decorEffect == DECOR_SPARKLE) {
    interval = effectInterval(260, 35);
  }
  if (now - decorLastUpdate < interval) return;
  decorLastUpdate = now;

  switch (decorEffect) {
    case DECOR_BLINK: {
      const uint8_t level = (decorStep++ % 2) ? 255 : 0;
      setLevels(level, level, level);
      break;
    }
    case DECOR_BREATHING:
      decorStep += decorDirection * 5;
      if (decorStep >= 255) { decorStep = 255; decorDirection = -1; }
      if (decorStep <= 0) { decorStep = 0; decorDirection = 1; }
      setLevels(decorStep, decorStep, decorStep);
      break;
    case DECOR_FADE_IN:
      decorStep = min(255, decorStep + 5);
      setLevels(decorStep, decorStep, decorStep);
      break;
    case DECOR_FADE_OUT:
      decorStep = max(0, decorStep - 5);
      setLevels(decorStep, decorStep, decorStep);
      break;
    case DECOR_SMOOTH:
    case DECOR_RAINBOW:
      smoothFrame(decorStep, decorEffect == DECOR_RAINBOW);
      decorStep = (decorStep + 4) % 768;
      break;
    case DECOR_CYCLE: {
      uint8_t levels[] = {0, 0, 0};
      levels[decorStep++ % 3] = 255;
      setLevels(levels[0], levels[1], levels[2]);
      break;
    }
    case DECOR_THEATER:
      if (decorStep++ % 2) setLevels(255, 0, 255);
      else setLevels(0, 255, 0);
      break;
    case DECOR_COMET:
    case DECOR_METEOR: {
      const uint8_t head = decorStep++ % 3;
      uint8_t levels[] = {0, 0, 0};
      levels[head] = 255;
      levels[(head + 2) % 3] = decorEffect == DECOR_METEOR ? 130 : 70;
      if (decorEffect == DECOR_METEOR) levels[(head + 1) % 3] = 35;
      setLevels(levels[0], levels[1], levels[2]);
      break;
    }
    case DECOR_SCANNER: {
      uint8_t levels[] = {0, 0, 0};
      levels[decorStep] = 255;
      if (decorStep > 0) levels[decorStep - 1] = 45;
      if (decorStep < 2) levels[decorStep + 1] = 45;
      setLevels(levels[0], levels[1], levels[2]);
      decorStep += decorDirection;
      if (decorStep >= 2) { decorStep = 2; decorDirection = -1; }
      if (decorStep <= 0) { decorStep = 0; decorDirection = 1; }
      break;
    }
    case DECOR_SPARKLE: {
      uint8_t levels[] = {0, 0, 0};
      levels[random(0, 3)] = random(100, 256);
      setLevels(levels[0], levels[1], levels[2]);
      break;
    }
    case DECOR_POLICE: {
      const uint8_t frame = decorStep++ % 8;
      if (frame < 2) setLevels(255, 0, 0);
      else if (frame < 4) setLevels(0, 0, 255);
      else if (frame < 6) setLevels(0, 255, 0);
      else setLevels(0, 0, 0);
      break;
    }
    default: break;
  }
}

void applyCommand(String value) {
  value.trim();
  value.toUpperCase();
  if (value == "DECOR_OFF") {
    stopDecor();
    Serial.println("OK");
    return;
  }
  if (value.startsWith("DECOR ")) {
    String parameters = value.substring(6);
    const int first = parameters.indexOf(' ');
    const int second = parameters.indexOf(' ', first + 1);
    if (first > 0 && second > first) {
      startDecor(
        parameters.substring(0, first),
        static_cast<uint8_t>(parameters.substring(first + 1, second).toInt()),
        static_cast<uint8_t>(parameters.substring(second + 1).toInt())
      );
    }
    return;
  }
  uint8_t beepCount = 0;
  const int separator = value.indexOf(' ');
  if (separator >= 0) {
    beepCount = static_cast<uint8_t>(constrain(value.substring(separator + 1).toInt(), 0, 3));
    value = value.substring(0, separator);
    value.trim();
  }
  if (value == "RED") {
    decorActive = false;
    setLights(true, false, false);
  } else if (value == "YELLOW") {
    decorActive = false;
    setLights(false, true, false);
  } else if (value == "GREEN") {
    decorActive = false;
    setLights(false, false, true);
  } else if (value == "OFF") {
    decorActive = false;
    setLights(false, false, false);
  } else {
    return;
  }
  // Acknowledge immediately so three-beep notifications cannot exceed the
  // desktop client's short request timeout.
  Serial.println("OK");
  if (beepCount > 0) {
    beep(beepCount);
  }
}

void processSerialCommand(String value) {
  value.trim();
  if (value == "CHALLENGE") {
    issueChallenge();
    return;
  }
  String plainCommand;
  if (!authenticateCommand(value, plainCommand)) {
    Serial.println("ERROR AUTH");
    return;
  }
  if (plainCommand == "INFO") {
    Serial.printf(
      "INFO %s %012llX\n", FIRMWARE_VERSION,
      static_cast<unsigned long long>(ESP.getEfuseMac() & 0xFFFFFFFFFFFFULL)
    );
    return;
  }
  if (plainCommand == "UPDATE_STATUS") {
    printFirmwareUpdateStatus();
    return;
  }
  if (plainCommand == "OTA_DEBUG") {
    printFirmwareDebug();
    return;
  }
  if (plainCommand.startsWith("UPDATE_TARGET ")) {
    const String target = plainCommand.substring(14);
    if (target.isEmpty() || target.length() > 32) {
      Serial.println("UPDATE ERROR VERSION");
      return;
    }
    preferences.begin("ai-status", false);
    preferences.putString("ota-target", target);
    preferences.end();
    Serial.println("OK");
    return;
  }
  if (plainCommand == "TEST_ROLLBACK_NEXT_BOOT") {
    preferences.begin("ai-status", false);
    preferences.putBool("ota-force", true);
    preferences.end();
    Serial.println("OK");
    return;
  }
  if (plainCommand == "TEST_ROLLBACK_CANCEL") {
    preferences.begin("ai-status", false);
    preferences.putBool("ota-force", false);
    preferences.end();
    Serial.println("OK");
    return;
  }
  if (plainCommand.startsWith("UPDATE_BEGIN ")) {
    beginFirmwareUpdate(plainCommand);
    return;
  }
  applyCommand(plainCommand);
}

void setup() {
  pinMode(RED_PIN, OUTPUT);
  pinMode(YELLOW_PIN, OUTPUT);
  pinMode(GREEN_PIN, OUTPUT);
  pinMode(BUZZER_PIN, OUTPUT);
  setLights(false, false, false);
  writeOutput(BUZZER_PIN, false);
  Serial.begin(115200);
  preferences.begin("ai-status", false);
  deviceAuthKey = preferences.getString("auth-key", "");
  const String factoryAuthKey = DEVICE_AUTH_KEY_HEX;
  if (deviceAuthKey.length() != 64 && factoryAuthKey.length() == 64) {
    deviceAuthKey = factoryAuthKey;
    preferences.putString("auth-key", deviceAuthKey);
  }
  preferences.end();
  confirmOrRollbackFirmware();
  randomSeed(micros());
}

void loop() {
  if (firmwareUpdateActive) {
    receiveFirmwareBytes();
    if (firmwareUpdateActive && millis() - firmwareLastByteAt > FIRMWARE_RECEIVE_TIMEOUT_MS) {
      abortFirmwareUpdate("UPDATE ERROR TIMEOUT", "timeout");
    }
    return;
  }
  while (Serial.available() > 0) {
    const char next = static_cast<char>(Serial.read());
    if (next == '\n' || next == '\r') {
      if (!command.isEmpty()) {
        processSerialCommand(command);
        command = "";
        // UPDATE_BEGIN switches the port from line commands to raw firmware
        // bytes. Return immediately so the command parser cannot consume the
        // first OTA block that may already be queued by the host.
        if (firmwareUpdateActive) return;
      }
    } else if (command.length() < 160) {
      command += next;
    }
  }
  updateDecor();
}
