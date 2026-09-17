<!DOCTYPE html>
<html lang="vi" class="gallery-page">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Thư viện | 47Square</title>
    <link rel="icon" href="{{ asset('images/logo/logo006.png') }}" type="image/png">
    <link rel="stylesheet" href="{{ asset('css/style.css') }}?v=11">
</head>
<body class="gallery-page">
    <div class="pattern-overlay"></div>
    <!-- Header riêng cho trang Thư viện -->
    <header class="gallery-header">
        <div class="gallery-header-left">
            <a href="{{ url('/') }}" class="gallery-logo-link">
                <img src="{{ asset('images/logo/logo005.png') }}" alt="47Square Logo" style="height: 60px; width: auto; object-fit: contain;">
            </a>
        </div>

        <div class="gallery-header-right">
            <nav class="gallery-nav">
                <ul>
                     <li><a href="{{ url('/thu-vien') }}" class="active">Sản phẩm</a></li>
                    <li><a href="{{ url('/') }}">Trang chủ</a></li>
                    <li><a href="#">Thư viện</a></li>                  
                    <li><a href="#">Liên hệ</a></li>
                </ul>
            </nav>
        </div>
    </header>

   

    <footer class="gallery-footer">
        © 2026 47Square. All rights reserved.
    </footer>
</body>
</html>
