package io.haneulmaru.bridge;

import android.app.Activity;
import android.content.Intent;
import android.os.Bundle;
import android.os.Handler;
import android.widget.*;

public class BridgeActivity extends Activity {
    private EditText host, port, key;
    private TextView state;
    private final Handler handler = new Handler();
    private final Runnable update = new Runnable() {
        public void run() { state.setText(BridgeService.status); handler.postDelayed(this, 500); }
    };
    private EditText field(LinearLayout box, String hint, String value){
        EditText e = new EditText(this); e.setSingleLine(true); e.setHint(hint); e.setText(value); box.addView(e); return e;
    }
    @Override public void onCreate(Bundle b){
        super.onCreate(b);
        LinearLayout box = new LinearLayout(this); box.setOrientation(1); box.setPadding(32,48,32,32);
        TextView help=new TextView(this); help.setText("NU40을 USB 데이터 케이블로 연결하고 PC의 Wi-Fi IPv4 주소와 페어링 키를 입력하세요."); box.addView(help);
        host=field(box,"PC Wi-Fi IPv4 주소",getPreferences(0).getString("host",""));
        port=field(box,"UDP 포트",getPreferences(0).getString("port","8793")); port.setInputType(2);
        key=field(box,"PC에 표시된 Pair key",""); key.setInputType(145);
        Button start=new Button(this); start.setText("USB → Wi-Fi 시작"); box.addView(start);
        start.setOnClickListener(v -> {
            String ip=host.getText().toString().trim(), secret=key.getText().toString().trim();
            int p;
            try { p=Integer.parseInt(port.getText().toString().trim()); } catch(NumberFormatException ex){p=0;}
            if (!android.util.Patterns.IP_ADDRESS.matcher(ip).matches() || !ip.contains(".") || p<1 || p>65535 || secret.length()<8 || secret.length()>64) {
                Toast.makeText(this,"PC IPv4 주소, UDP 포트와 8~64자 키를 확인하세요.",Toast.LENGTH_LONG).show(); return;
            }
            getPreferences(0).edit().putString("host",ip).putString("port",Integer.toString(p)).apply();
            startForegroundService(new Intent(this,BridgeService.class).putExtra("host",ip).putExtra("port",p).putExtra("key",secret));
        });
        Button stop=new Button(this); stop.setText("중지"); box.addView(stop);
        stop.setOnClickListener(v->stopService(new Intent(this,BridgeService.class)));
        state=new TextView(this); box.addView(state); setContentView(box);
    }
    @Override protected void onResume(){super.onResume();handler.post(update);}
    @Override protected void onPause(){handler.removeCallbacks(update);super.onPause();}
}
