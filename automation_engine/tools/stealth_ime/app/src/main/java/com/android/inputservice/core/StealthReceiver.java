package com.android.inputservice.core;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.util.Base64;
import android.util.Log;

/**
 * 广播接收器 — 监听伪装后的 Action，将指令转发给 StealthIME 执行文本注入。
 *
 * 使用显式广播（指定 -n 组件名）发送，绕过 Android 8.0+ 隐式广播限制。
 *
 * ADB 使用示例（必须加 -n 指定组件）：
 *   adb shell am broadcast -n com.android.inputservice.core/.StealthReceiver -a com.android.input.COMMIT --es msg "你好"
 */
public class StealthReceiver extends BroadcastReceiver {

    private static final String TAG = "SysInput";

    private static final String ACTION_COMMIT  = "com.android.input.COMMIT";
    private static final String ACTION_SYNC    = "com.android.input.SYNC";
    private static final String ACTION_EVENT   = "com.android.input.EVENT";
    private static final String ACTION_CLEAR   = "com.android.input.CLEAR";
    private static final String ACTION_REPLACE = "com.android.input.REPLACE";
    private static final String ACTION_EDITOR  = "com.android.input.EDITOR";

    @Override
    public void onReceive(Context context, Intent intent) {
        Log.d(TAG, "Broadcast received: " + intent.getAction());

        StealthIME ime = StealthIME.getInstance();
        if (ime == null) {
            Log.w(TAG, "IME service not active");
            return;
        }

        String action = intent.getAction();
        if (action == null) return;

        switch (action) {
            case ACTION_COMMIT: {
                String msg = intent.getStringExtra("msg");
                if (msg != null) {
                    ime.commitTextInternal(msg);
                }
                break;
            }
            case ACTION_SYNC: {
                String b64 = intent.getStringExtra("msg");
                if (b64 != null) {
                    try {
                        String decoded = new String(Base64.decode(b64, Base64.DEFAULT), "UTF-8");
                        ime.commitTextInternal(decoded);
                    } catch (Exception e) {
                        Log.e(TAG, "B64 decode failed", e);
                    }
                }
                break;
            }
            case ACTION_EVENT: {
                int code = intent.getIntExtra("code", -1);
                if (code != -1) {
                    ime.sendKeyCodeInternal(code);
                }
                break;
            }
            case ACTION_CLEAR: {
                ime.clearTextInternal();
                break;
            }
            case ACTION_REPLACE: {
                String msg = intent.getStringExtra("msg");
                ime.setTextInternal(msg != null ? msg : "");
                break;
            }
            case ACTION_EDITOR: {
                int code = intent.getIntExtra("code", -1);
                if (code != -1) {
                    ime.performEditorActionInternal(code);
                }
                break;
            }
        }
    }
}
