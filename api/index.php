<?php
ini_set('display_errors', 1);
error_reporting(E_ALL);

// Ensure storage structure exists in /tmp because Vercel is read-only
$storagePath = '/tmp/storage';
$dirs = [
    $storagePath . '/app',
    $storagePath . '/framework/cache',
    $storagePath . '/framework/sessions',
    $storagePath . '/framework/views',
    $storagePath . '/logs',
];
foreach ($dirs as $dir) {
    if (!is_dir($dir)) {
        mkdir($dir, 0777, true);
    }
}

$publicIndex = __DIR__ . '/../public/index.php';
if (!file_exists($publicIndex)) {
    die("Error: public/index.php not found at " . $publicIndex);
}
require $publicIndex;
