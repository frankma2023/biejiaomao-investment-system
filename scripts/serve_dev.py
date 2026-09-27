"""
投资系统 Web 前门：单端口同时提供静态页面与 API 反向代理。

为什么需要它
------------
前端页面在 :8772、API 在 :8788，是两个端口。本机浏览器没问题，但：

1. **隧道场景必须合并成一个端口** —— Pinggy / Cloudflare 等给的公网地址只映射一个端口；
2. **HTTPS 页面调 http://…:8788 会被浏览器按「混合内容」拦掉** —— 走 https 隧道时，
   API 必须与页面同源；
3. 同源之后 **CORS 也不再需要**。

所以这里把 `/api/*` 反代到 127.0.0.1:8788，其余按静态文件从 web/ 提供。
前端因此可以用**相对路径** `/api/xxx`，在 localhost / 局域网 IPv4 / 公网 IPv6 /
隧道 HTTPS 下都一样工作。

用法
----
    python scripts/serve_dev.py [端口] [目录]        # 默认 8772 web
    # 然后暴露单个端口即可，例如 Pinggy（DSH Desktop 用的就是它）：
    #   ssh -p 443 -R0:127.0.0.1:8772 free.pinggy.io
"""
import contextlib
import http.client
import os
import socket
import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

API_HOST, API_PORT = '127.0.0.1', 8788


class NoCacheHandler(SimpleHTTPRequestHandler):
    """所有 .html / .js / .css 强制 no-cache，改完刷新即可见。"""

    def end_headers(self):
        path = self.path
        is_html = path.endswith('.html') or path.endswith('/') or '.' not in path.split('/')[-1]
        if is_html or path.endswith('.js') or path.endswith('.css'):
            self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
            self.send_header('Pragma', 'no-cache')
            self.send_header('Expires', '0')
        super().end_headers()

    # ── /api/* → Flask(:8788) ────────────────────────────────
    def _is_api(self):
        return self.path == '/api' or self.path.startswith('/api/')

    def _proxy(self):
        length = int(self.headers.get('Content-Length') or 0)
        body = self.rfile.read(length) if length else None
        headers = {}
        for k in ('Content-Type', 'Accept', 'Authorization'):
            v = self.headers.get(k)
            if v:
                headers[k] = v
        try:
            conn = http.client.HTTPConnection(API_HOST, API_PORT, timeout=180)
            conn.request(self.command, self.path, body=body, headers=headers)
            resp = conn.getresponse()
            payload = resp.read()
            self.send_response(resp.status)
            for k, v in resp.getheaders():
                # Content-Length 由 send_response 之后统一给，避免重复
                if k.lower() in ('content-length', 'transfer-encoding', 'connection'):
                    continue
                self.send_header(k, v)
            self.send_header('Content-Length', str(len(payload)))
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(payload)
            conn.close()
        except Exception as e:
            msg = ('后端 API(:%d) 未启动或不可达：%s' % (API_PORT, e)).encode('utf-8')
            self.send_response(502)
            self.send_header('Content-Type', 'text/plain; charset=utf-8')
            self.send_header('Content-Length', str(len(msg)))
            self.end_headers()
            self.wfile.write(msg)

    def _dispatch(self):
        if self._is_api():
            return self._proxy()
        return super().do_GET() if self.command == 'GET' else self.send_error(405)

    do_GET = do_HEAD = do_POST = do_PUT = do_DELETE = do_PATCH = _dispatch

    def log_message(self, fmt, *args):
        # 只记 API 与错误，静态请求不刷屏
        if self._is_api() or (args and str(args[1]).startswith(('4', '5'))):
            sys.stderr.write('  %s - %s\n' % (self.address_string(), fmt % args))


class DualStackServer(ThreadingHTTPServer):
    """绑 :: 但同时收 IPv4。

    Windows 的 IPV6_V6ONLY 默认是 1，裸绑 :: 会变成「仅 IPv6」，
    反而让 localhost 连不上；必须在 bind 之前显式设 0
    （Python 自带 http.server 的 DualStackServer 就是这么做的）。
    """

    address_family = socket.AF_INET6

    def server_bind(self):
        with contextlib.suppress(Exception):
            self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        super().server_bind()


if __name__ == '__main__':
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8772
    directory = sys.argv[2] if len(sys.argv) > 2 else 'web'
    os.chdir(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), directory))
    srv = DualStackServer(('::', port), NoCacheHandler)
    print('前门已启动  静态 %s/  |  /api/* -> %s:%d' % ('web', API_HOST, API_PORT))
    print('  本机     http://localhost:%d/' % port)
    print('  隧道     ssh -p 443 -R0:127.0.0.1:%d free.pinggy.io' % port)
    srv.serve_forever()
