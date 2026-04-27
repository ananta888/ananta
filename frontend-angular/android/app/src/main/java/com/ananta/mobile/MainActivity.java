package com.ananta.mobile;

import android.os.Bundle;

import com.getcapacitor.BridgeActivity;
import com.ananta.mobile.python.PythonRuntimePlugin;
import com.ananta.mobile.voxtral.VoxtralOfflinePlugin;

public class MainActivity extends BridgeActivity {
    @Override
    public void onCreate(Bundle savedInstanceState) {
        registerPlugin(VoxtralOfflinePlugin.class);
        registerPlugin(PythonRuntimePlugin.class);
        super.onCreate(savedInstanceState);
    }
}
