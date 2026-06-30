"""
静态文件服务器，所有 .html 文件强制 no-cache
启动：python scripts/serve_dev.py
"""
import os, sys
from http.server import HTTPServer, SimpleHTTPRequestHandler

class NoCacheHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        path = self.path
        is_html = path.endswith('.html') or path.endswith('/') or '.' not in path.split('/')[-1]
        if is_html or path.endswith('.js') or path.endswith('.css'):
            self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
            self.send_header('Pragma', 'no-cache')
            self.send_header('Expires', '0')
        super().end_headers()

if __name__ == '__main__':
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8772
    directory = sys.argv[2] if len(sys.argv) > 2 else 'web'
    os.chdir(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), directory))
    server = HTTPServer(('0.0.0.0', port), NoCacheHandler)
    print(f'开发服务器 http://localhost:{port}  (no-cache)')
    server.serve_forever()
