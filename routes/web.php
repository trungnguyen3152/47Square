<?php

use Illuminate\Support\Facades\Route;

Route::get('/', function () {
    return view('home');
});

Route::get('/home', function () {
    return view('home');
});

Route::get('/thu-vien', function () {
    return view('gallery');
});

Route::get('/coming-soon', function () {
    return view('coming-soon');
});
