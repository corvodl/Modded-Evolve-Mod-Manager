"""Loopback-only response simulator for authorized Evolve developer fixtures.

Never intercepts gameplay traffic, impersonates remote authentication services,
or changes real client endpoints. Only the developer can configure a separate
private test client's URL to use 127.0.0.1 and this local fixture server.
"""
from __future__ import annotations
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
import json
import threading

MAX_FIXTURE=64*1024
MAX_ROUTES=50


def validate_routes(routes):
    if not isinstance(routes,dict) or len(routes)>MAX_ROUTES:
        raise ValueError('Specify up to 50 local fixture routes')
    valid={}
    for path,result in routes.items():
        if (not isinstance(path,str) or not path.startswith('/') or
                '://' in path or len(path)>120 or '..' in path):
            raise ValueError('Route must be a short relative HTTP path')
        data=json.dumps(result,ensure_ascii=False).encode('utf-8')
        if len(data)>MAX_FIXTURE:
            raise ValueError('Response body exceeds 64 KiB.')
        valid[path]=data
    return valid


class LocalFixtureService:
    def __init__(self,routes=None,port=0):
        self.routes=validate_routes(routes or {'/health':{'status':'ok','source':'localhost'}})
        if not 0<=int(port)<=65535:
            raise ValueError('Unsupported loopback TCP port')
        outer=self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                path=self.path.split('?')[0]
                body=outer.routes.get(path)
                if body is None:
                    body=b'{"error":"fixture route not found"}'
                    code=404
                else:code=200
                self.send_response(code)
                self.send_header('Content-Type','application/json; charset=utf-8')
                self.send_header('Cache-Control','no-store')
                self.send_header('Content-Length',str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                self.send_error(405,'Mock fixtures are GET only')

            def log_message(self,*args):
                pass

        self.server=ThreadingHTTPServer(('127.0.0.1',int(port)),Handler)
        self.thread=None

    @property
    def endpoint(self):
        return 'http://127.0.0.1:%d'%self.server.server_address[1]

    def start(self):
        if self.thread:
            raise RuntimeError('Already started')
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        return self.endpoint

    def stop(self):
        if self.thread:
            self.server.shutdown()
            self.thread.join(timeout=5)
            self.thread=None
        self.server.server_close()
