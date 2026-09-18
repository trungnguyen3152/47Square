import Link from 'next/link';

export default function Home() {
  return (
    <div className="gallery-page">
      <div className="pattern-overlay"></div>
      <header className="gallery-header">
        <div className="gallery-header-left">
          <Link href="/" className="gallery-logo-link">
            <img 
              src="/images/logo/logo005.png" 
              alt="47Square Logo" 
              style={{ height: '60px', width: 'auto', objectFit: 'contain' }} 
            />
          </Link>
        </div>

        <div className="gallery-header-right">
          <nav className="gallery-nav">
            <ul>
              <li><Link href="/thu-vien" className="active">Sản phẩm</Link></li>
              <li><Link href="/">Trang chủ</Link></li>
              <li><Link href="#">Thư viện</Link></li>                  
              <li><Link href="#">Liên hệ</Link></li>
            </ul>
          </nav>
        </div>
      </header>

      <footer className="gallery-footer">
        © 2026 47Square. All rights reserved.
      </footer>
    </div>
  );
}
