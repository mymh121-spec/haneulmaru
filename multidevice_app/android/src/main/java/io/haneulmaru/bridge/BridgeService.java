package io.haneulmaru.bridge;

import android.app.*;
import android.content.*;
import android.hardware.usb.*;
import android.net.*;
import android.os.*;
import org.json.*;
import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.util.UUID;

public class BridgeService extends Service {
    static volatile String status="중지됨";
    private static final String PERMISSION="io.haneulmaru.bridge.USB_PERMISSION";
    private Thread worker;
    private int generation;
    private volatile int requestedId=-1;
    private final BroadcastReceiver attached=new BroadcastReceiver(){
        @Override public void onReceive(Context c,Intent i){
            if (UsbManager.ACTION_USB_DEVICE_DETACHED.equals(i.getAction())) requestedId=-1;
            if (PERMISSION.equals(i.getAction()) && !i.getBooleanExtra(UsbManager.EXTRA_PERMISSION_GRANTED,false))
                status="USB 권한이 필요합니다. 시작을 다시 누르세요.";
        }
    };
    @Override public void onCreate(){
        super.onCreate();
        NotificationManager nm=getSystemService(NotificationManager.class);
        nm.createNotificationChannel(new NotificationChannel("usb","NU40 USB",NotificationManager.IMPORTANCE_LOW));
        IntentFilter filter=new IntentFilter(PERMISSION);filter.addAction(UsbManager.ACTION_USB_DEVICE_DETACHED);
        if(Build.VERSION.SDK_INT>=33)registerReceiver(attached,filter,Context.RECEIVER_NOT_EXPORTED);else registerReceiver(attached,filter);
    }
    @Override public int onStartCommand(Intent i,int flags,int id){
        Notification notice=new Notification.Builder(this,"usb").setSmallIcon(android.R.drawable.stat_notify_sync)
            .setContentTitle("하늘마루 USB → Wi-Fi").setContentText("USB 센서값을 PC로 전송합니다.")
            .setContentIntent(PendingIntent.getActivity(this,0,new Intent(this,BridgeActivity.class),PendingIntent.FLAG_IMMUTABLE))
            .setOngoing(true).build();
        startForeground(1,notice);
        if(i==null || i.getStringExtra("host")==null || i.getStringExtra("key")==null){stopSelf();return START_NOT_STICKY;}
        if(worker!=null)worker.interrupt();
        final int run=++generation; final Thread prior=worker; requestedId=-1;
        worker=new Thread(()->{
            try{
                if(prior!=null)prior.join();
                relay(run,i.getStringExtra("host"),i.getIntExtra("port",8793),i.getStringExtra("key"));
            }catch(InterruptedException ex){Thread.currentThread().interrupt();}
        },"NU40-USB");
        worker.start(); return START_NOT_STICKY;
    }
    private void relay(int run,String host,int port,String key) throws InterruptedException{
        UsbManager usb=getSystemService(UsbManager.class);
        while(!Thread.currentThread().isInterrupted()){
            UsbDevice device=null; int matches=0;
            for(UsbDevice d:usb.getDeviceList().values()) if(d.getVendorId()==0x239a && d.getProductId()==0x80da){device=d;matches++;}
            if(matches!=1){requestedId=-1;status=matches==0?"NU40 USB 연결 대기":"NU40 한 대만 연결하세요.";}
            else if(!usb.hasPermission(device)){
                if(requestedId!=device.getDeviceId()){
                    requestedId=device.getDeviceId();status="NU40 USB 권한 확인 중";
                    Intent intent=new Intent(PERMISSION).setPackage(getPackageName());
                    usb.requestPermission(device,PendingIntent.getBroadcast(this,0,intent,PendingIntent.FLAG_IMMUTABLE));
                }
            }else{
                try{readUsb(usb,device,host,port,key);}
                catch(IOException | SecurityException ex){if(!Thread.currentThread().isInterrupted())status="다시 연결 중 · "+ex.getMessage();}
            }
            Thread.sleep(500);
        }
    }
    private void readUsb(UsbManager usb,UsbDevice device,String host,int port,String key) throws IOException{
        UsbInterface control=null,data=null;
        UsbEndpoint in=null;
        for(int i=0;i<device.getInterfaceCount();i++){
            UsbInterface iface=device.getInterface(i);
            if(control==null && iface.getInterfaceClass()==UsbConstants.USB_CLASS_COMM)control=iface;
            if(data==null && iface.getInterfaceClass()==UsbConstants.USB_CLASS_CDC_DATA){
                for(int e=0;e<iface.getEndpointCount();e++){
                    UsbEndpoint ep=iface.getEndpoint(e);
                    if(ep.getDirection()==UsbConstants.USB_DIR_IN && ep.getType()==UsbConstants.USB_ENDPOINT_XFER_BULK){data=iface;in=ep;break;}
                }
            }
        }
        if(control==null || in==null)throw new IOException("USB CDC 인터페이스를 찾지 못했습니다");
        UsbDeviceConnection link=usb.openDevice(device);
        if(link==null)throw new IOException("NU40을 열 수 없습니다");
        try(DatagramSocket udp=new DatagramSocket()){
            ConnectivityManager cm=getSystemService(ConnectivityManager.class);
            for(Network network:cm.getAllNetworks()){
                NetworkCapabilities caps=cm.getNetworkCapabilities(network);
                if(caps!=null && caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)){network.bindSocket(udp);break;}
            }
            InetAddress address=InetAddress.getByName(host);
            if(!link.claimInterface(control,true)||!link.claimInterface(data,true))throw new IOException("USB 인터페이스 사용 중");
            byte[] format={0,(byte)0xc2,1,0,0,0,8}; // CDC 115200, 8N1
            if(link.controlTransfer(0x21,0x20,0,control.getId(),format,7,500)!=7 ||
               link.controlTransfer(0x21,0x22,1,control.getId(),null,0,500)<0)throw new IOException("USB CDC 설정 실패");
            byte[] buffer=new byte[2048];ByteArrayOutputStream line=new ByteArrayOutputStream();
            boolean discard=false;String session=UUID.randomUUID().toString();long seq=0,last=SystemClock.elapsedRealtime();
            status="USB 연결됨 · 센서 대기";
            while(!Thread.currentThread().isInterrupted()){
                int count=link.bulkTransfer(in,buffer,buffer.length,250);
                if(count>0){
                    for(int i=0;i<count;i++){
                        if(buffer[i]==10){
                            if(!discard && line.size()>0){
                                try{
                                    JSONObject packet=new JSONObject(line.toString("UTF-8"));
                                    if(packet.optInt("v")==1 || ("imu".equals(packet.optString("type")) && packet.optInt("schema")==1)){
                                        JSONObject env=new JSONObject().put("key",key).put("session",session).put("seq",++seq).put("packet",packet);
                                        byte[] bytes=env.toString().getBytes(StandardCharsets.UTF_8);
                                        if(bytes.length<=4096){
                                            udp.send(new DatagramPacket(bytes,bytes.length,address,port));last=SystemClock.elapsedRealtime();
                                            status="USB 수신 · "+host+":"+port+" 전송 "+seq+"건";
                                        }
                                    }
                                }catch(JSONException ignored){ /* Firmware also writes diagnostic lines. */ }
                            }
                            line.reset();discard=false;
                        }else if(!discard && line.size()<3072)line.write(buffer[i]);else discard=true;
                    }
                }
                if(SystemClock.elapsedRealtime()-last>3500)throw new IOException("USB 데이터 시간 초과");
            }
        }finally{link.close();}
    }
    @Override public void onDestroy(){if(worker!=null)worker.interrupt();unregisterReceiver(attached);status="중지됨";super.onDestroy();}
    @Override public IBinder onBind(Intent i){return null;}
}
