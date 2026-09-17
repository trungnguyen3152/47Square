<?php
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

require __DIR__ . '/../public/index.php';
