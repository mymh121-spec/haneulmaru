"""PC rendered WebRTC mode, separate from 8790 and 8791. Signaling only."""
import server
from http.server import ThreadingHTTPServer
from urllib.parse import urlsplit,parse_qs
import secrets,json,threading,subprocess,sys,re,webbrowser,base64,time
from collections import deque
HOST=secrets.token_urlsafe(24);PHONE=secrets.token_urlsafe(24);URL='';LOCK=threading.Lock();MESSAGES=deque(maxlen=256);SEQ=0
ASSETS=dict(server.ASSETS)
ASSETS.update({'/'+n:server.ROOT/n for n in ['rtc.js','host-stream.js','receiver.js','receiver.html']})
ASSETS['/host']=server.ROOT/'index.html';ASSETS['/phone']=server.ROOT/'receiver.html';ASSETS['/']=server.ROOT/'receiver.html'
class Handler(server.Handler):
 def role(self):
  value=self.headers.get('Authorization','')
  if secrets.compare_digest(value,'Bearer '+HOST):return 'host'
  if secrets.compare_digest(value,'Bearer '+PHONE):return 'phone'
 def send_json(self,status,value):return self.send(status,json.dumps(value).encode(),'application/json')
 def do_GET(self):
  path=urlsplit(self.path).path
  if path.startswith('/stream/'):
   role=self.role()
   if not role:return self.send_json(401,{'error':'unauthorized'})
   if path=='/stream/info' and role=='host':
    url=(URL or 'http://127.0.0.1:8792')+'/phone#'+PHONE
    from reportlab.graphics.barcode import qr
    from reportlab.graphics.shapes import Drawing
    from reportlab.graphics import renderSVG
    code=qr.QrCodeWidget(url);x,y,w,h=code.getBounds();drawing=Drawing(w,h);drawing.add(code)
    return self.send_json(200,{'url':url,'qr':base64.b64encode(renderSVG.drawToString(drawing).encode()).decode()})
   if path=='/stream/signal':
    try:after=int(parse_qs(urlsplit(self.path).query).get('after',['0'])[0])
    except ValueError:return self.send_json(400,{'error':'cursor'})
    with LOCK:messages=[{'id':i,'message':m} for i,r,m,t in MESSAGES if r!=role and i>after and time.monotonic()-t<300]
    return self.send_json(200,messages)
   return self.send_json(404,{})
  f=ASSETS.get(path)
  if not f or not f.is_file():return self.send(404,b'Not found','text/plain')
  kind={'js':'text/javascript','mjs':'text/javascript','json':'application/json','webmanifest':'application/manifest+json'}.get(f.suffix[1:],server.mimetypes.guess_type(f.name)[0] or 'application/octet-stream')
  return self.send(200,f.read_bytes(),kind)
 def do_POST(self):
  global SEQ
  role=self.role()
  if not role:return self.send_json(401,{})
  if urlsplit(self.path).path!='/stream/signal':return self.send_json(404,{})
  try:
   size=int(self.headers.get('Content-Length','0'))
   if not 0<size<=200000:raise ValueError()
   message=json.loads(self.rfile.read(size))
   if not isinstance(message,dict) or message.get('type') not in ({'offer','ice'} if role=='host' else {'ready','answer','ice'}):raise ValueError()
   if not isinstance(message.get('session'),str) or not 1<=len(message['session'])<=80:raise ValueError()
  except (ValueError,TypeError):return self.send_json(400,{'error':'invalid signal'})
  with LOCK:SEQ+=1;MESSAGES.append((SEQ,role,message,time.monotonic()))
  return self.send_json(200,{'ok':True})
def main():
 global URL
 try:http=ThreadingHTTPServer(('127.0.0.1',8792),Handler)
 except OSError:print('Port 8792 occupied. Existing stream server may be running.',flush=True);return 1
 threading.Thread(target=http.serve_forever,daemon=True).start();host='http://127.0.0.1:8792/host#'+HOST
 print('PC: '+host,flush=True)
 if '--no-browser' not in sys.argv:webbrowser.open(host)
 exe=server.ROOT.parents[1]/'tools/cloudflared/cloudflared.exe'
 child=subprocess.Popen([str(exe),'tunnel','--no-autoupdate','--url','http://127.0.0.1:8792','--protocol','http2'],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace',creationflags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0)
 try:
  for line in child.stdout:
   m=re.search(r'https://[a-z0-9-]+\.trycloudflare\.com',line)
   if m and not URL:URL=m.group(0);print('PHONE: '+URL+'/phone#'+PHONE,flush=True)
   if 'ERR' in line:print(line.strip(),flush=True)
 except KeyboardInterrupt:pass
 finally:
  child.terminate()
  try:child.wait(timeout=5)
  except subprocess.TimeoutExpired:child.kill();child.wait()
  http.shutdown();http.server_close()
 return 0
if __name__=='__main__':sys.exit(main())
