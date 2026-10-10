"""Independent static PRO app on loopback 8791. No camera or control uploads."""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit
import json,mimetypes,threading,subprocess,re,sys,webbrowser
ROOT=Path(__file__).resolve().parent
PUBLIC_URL=''
FILES=['index.html','style.css','app.js','drone-coordinates.js','coordinate-ui.js','imu-input.js','imu-direction.js','world.js','terrain.js','sw.js','manifest.webmanifest','icon-192.png','icon-512.png','README.html','vendor/three.module.min.js','vendor/three.core.min.js','vendor/OrbitControls.js','vendor/LICENSE.txt']
ASSETS={'/'+n:ROOT/n for n in FILES}
ASSETS['/']=ROOT/'index.html'
class Handler(BaseHTTPRequestHandler):
 def log_message(self,*args):pass
 def send(self,status,data,kind):
  self.send_response(status);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-cache');self.send_header('X-Content-Type-Options','nosniff');self.send_header('Referrer-Policy','no-referrer');self.end_headers()
  try:self.wfile.write(data)
  except (BrokenPipeError,ConnectionResetError):pass
 def do_GET(self):
  path=urlsplit(self.path).path
  if path=='/info':return self.send(200,json.dumps({'url':PUBLIC_URL}).encode(),'application/json')
  if path=='/qr':
   if not PUBLIC_URL:return self.send(204,b'','image/svg+xml')
   try:
    from reportlab.graphics.barcode import qr
    from reportlab.graphics.shapes import Drawing
    from reportlab.graphics import renderSVG
    code=qr.QrCodeWidget(PUBLIC_URL);x,y,w,h=code.getBounds();drawing=Drawing(w,h);drawing.add(code)
    return self.send(200,renderSVG.drawToString(drawing).encode('utf-8'),'image/svg+xml')
   except ImportError:return self.send(503,b'QR unavailable; use URL','text/plain')
  f=ASSETS.get(path)
  if not f or not f.is_file():return self.send(404,b'Not found','text/plain')
  kind={'js':'text/javascript','webmanifest':'application/manifest+json'}.get(f.suffix[1:],mimetypes.guess_type(f.name)[0] or 'application/octet-stream')
  return self.send(200,f.read_bytes(),kind)
def main():
 global PUBLIC_URL
 try:http=ThreadingHTTPServer(('127.0.0.1',8791),Handler)
 except OSError:print('Port 8791 occupied. Existing PRO server may already be running.',flush=True);return 1
 threading.Thread(target=http.serve_forever,daemon=True).start()
 print('PC: http://127.0.0.1:8791',flush=True)
 if '--no-browser' not in sys.argv:webbrowser.open('http://127.0.0.1:8791')
 child=None
 try:
  exe=ROOT.parents[1]/'tools/cloudflared/cloudflared.exe'
  if '--local' in sys.argv or not exe.exists():
   print('Local mode. Phone AR requires HTTPS.',flush=True);threading.Event().wait()
  else:
   child=subprocess.Popen([str(exe),'tunnel','--no-autoupdate','--url','http://127.0.0.1:8791','--protocol','http2'],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace',creationflags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0)
   for line in child.stdout:
    m=re.search(r'https://[a-z0-9-]+\.trycloudflare\.com',line)
    if m and not PUBLIC_URL:PUBLIC_URL=m.group(0);print('PHONE: '+PUBLIC_URL,flush=True)
    if 'ERR' in line:print(line.strip(),flush=True)
 except KeyboardInterrupt:pass
 finally:
  if child:
   child.terminate()
   try:child.wait(timeout=5)
   except subprocess.TimeoutExpired:child.kill();child.wait()
  http.shutdown();http.server_close()
 return 0
if __name__=='__main__':sys.exit(main())
