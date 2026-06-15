package com.android.inputservice.core;

import android.inputmethodservice.InputMethodService;
import android.util.Log;
import android.view.KeyEvent;
import android.view.View;
import android.view.inputmethod.EditorInfo;
import android.view.inputmethod.InputConnection;

/**
 * Stealth IME — 伪装为系统输入服务的隐蔽输入法。
 *
 * BroadcastReceiver 采用 Manifest 静态注册 + 显式广播发送的方式，
 * 完美兼容 Android 8.0+ 的隐式广播限制。
 */
public class StealthIME extends InputMethodService {

    private static final String TAG = "SysInput";
    private static StealthIME sInstance;

    @Override
    public void onCreate() {
        super.onCreate();
        sInstance = this;
        Log.d(TAG, "Service created");
    }

    @Override
    public View onCreateInputView() {
        android.widget.LinearLayout layout = new android.widget.LinearLayout(this);
        int height = (int) (300 * getResources().getDisplayMetrics().density);
        layout.setLayoutParams(new android.view.ViewGroup.LayoutParams(
                android.view.ViewGroup.LayoutParams.MATCH_PARENT, height));
        // 使用一个极度浅灰色的背景，充当实体键盘
        layout.setBackgroundColor(android.graphics.Color.parseColor("#EAEAEA"));
        layout.setOrientation(android.widget.LinearLayout.VERTICAL);
        layout.setGravity(android.view.Gravity.CENTER);

        android.widget.TextView tv = new android.widget.TextView(this);
        tv.setText("Stealth IME (Virtual Keyboard Active)");
        tv.setTextColor(android.graphics.Color.GRAY);
        layout.addView(tv);

        return layout;
    }

    @Override
    public void onStartInputView(EditorInfo info, boolean restarting) {
        super.onStartInputView(info, restarting);
        Log.d(TAG, "Input view started");
    }

    @Override
    public void onDestroy() {
        sInstance = null;
        super.onDestroy();
        Log.d(TAG, "Service destroyed");
    }

    static StealthIME getInstance() {
        return sInstance;
    }

    void commitTextInternal(String text) {
        InputConnection ic = getCurrentInputConnection();
        if (ic != null && text != null) {
            ic.commitText(text, 1);
        }
    }

    void sendKeyCodeInternal(int keyCode) {
        InputConnection ic = getCurrentInputConnection();
        if (ic != null) {
            ic.sendKeyEvent(new KeyEvent(KeyEvent.ACTION_DOWN, keyCode));
            ic.sendKeyEvent(new KeyEvent(KeyEvent.ACTION_UP, keyCode));
        }
    }

    void performEditorActionInternal(int actionCode) {
        InputConnection ic = getCurrentInputConnection();
        if (ic != null) {
            ic.performEditorAction(actionCode);
        }
    }

    void clearTextInternal() {
        InputConnection ic = getCurrentInputConnection();
        if (ic != null) {
            ic.performContextMenuAction(android.R.id.selectAll);
            ic.commitText("", 0);
        }
    }

    void setTextInternal(String text) {
        clearTextInternal();
        if (text != null && !text.isEmpty()) {
            commitTextInternal(text);
        }
    }
}
