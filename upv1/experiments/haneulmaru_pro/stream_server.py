"""PC rendered WebRTC mode, separate from 8790 and 8791. Signaling plus authenticated NU40 input."""
import server
from http.server import ThreadingHTTPServer
from urllib.parse import urlsplit,parse_qs
import secrets,json,threading,subprocess,sys,re,webbrowser,base64,time
from collections import deque
import argparse,shutil
from nu40_imu import ImuHub
IMU=None
MODEL=server.ROOT.parents[1]/"multidevice_app"/"assets"/"nu40-imu-direction-training.json"
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
   if path in ('/stream/imu','/stream/imu-model'):
    if role!='host':return self.send_json(403,{'error':'host only'})
    if path=='/stream/imu':
     if IMU is None:return self.send_json(503,{'error':'IMU disabled'})
     return self.send_json(200,IMU.snapshot())
    try:
     model=json.loads(MODEL.read_text(encoding='utf-8'))
     return self.send_json(200,{k:model[k] for k in ('format','schema_version','units','directions','rate_threshold_dps','similarity_threshold')})
    except (OSError,ValueError,KeyError):return self.send_json(503,{'error':'direction model unavailable'})
   if path=='/stream/info' and role=='host':
    url=(URL or f'http://127.0.0.1:{self.server.server_port}')+'/phone#'+PHONE
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
  kind={'js':'text/javascript','webmanifest':'application/manifest+json'}.get(f.suffix[1:],server.mimetypes.guess_type(f.name)[0] or 'application/octet-stream')
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
 global URL,IMU
 parser=argparse.ArgumentParser(description='Haneulmaru with NU40 USB/BLE IMU')
 parser.add_argument('--local',action='store_true',help='Run without a public tunnel')
 parser.add_argument('--no-browser',action='store_true')
 parser.add_argument('--port',type=int,default=8792)
 parser.add_argument('--imu-port',help='NU40 USB port; auto-detects by USB VID/PID by default')
 parser.add_argument('--ble-address',help='Optional BLE address for selecting one NU40')
 parser.add_argument('--no-imu',action='store_true')
 parser.add_argument('--no-ble',action='store_true')
 args=parser.parse_args()
 try:http=ThreadingHTTPServer(('127.0.0.1',args.port),Handler)
 except OSError:print(f'Port {args.port} occupied. Existing stream server may be running.',flush=True);return 1
 child=None
 if not args.no_imu:
  IMU=ImuHub(port=args.imu_port,ble_address=args.ble_address,ble=not args.no_ble)
  IMU.start()
 threading.Thread(target=http.serve_forever,daemon=True).start()
 host=f'http://127.0.0.1:{args.port}/host#'+HOST
 print('PC: '+host,flush=True)
 if not args.no_browser:webbrowser.open(host)
 exe=server.ROOT.parents[1]/'tools/cloudflared/cloudflared.exe' if sys.platform=='win32' else shutil.which('cloudflared')
 try:
  if args.local or not exe or not server.Path(exe).is_file():
   print('Local mode: NU40 USB preferred; BLE remains connected.',flush=True)
   threading.Event().wait()
  else:
   child=subprocess.Popen([str(exe),'tunnel','--no-autoupdate','--url',f'http://127.0.0.1:{args.port}','--protocol','http2'],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace',creationflags=subprocess.CREATE_NO_WINDOW if sys.platform=='win32' else 0)
   for line in child.stdout:
    m=re.search(r'https://[a-z0-9-]+\.trycloudflare\.com',line)
    if m and not URL:URL=m.group(0);print('PHONE: '+URL+'/phone#'+PHONE,flush=True)
    if 'ERR' in line:print(line.strip(),flush=True)
 except KeyboardInterrupt:pass
 finally:
  if child:
   child.terminate()
   try:child.wait(timeout=5)
   except subprocess.TimeoutExpired:child.kill();child.wait()
  if IMU:IMU.stop()
  http.shutdown();http.server_close()
 return 0
if __name__=='__main__':sys.exit(main())
