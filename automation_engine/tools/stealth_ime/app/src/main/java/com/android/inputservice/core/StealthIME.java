package com.android.inputservice.core;

import android.inputmethodservice.InputMethodService;
import android.net.LocalServerSocket;
import android.net.LocalSocket;
import android.util.Base64;
import android.util.Log;
import android.view.KeyEvent;
import android.view.View;
import android.view.inputmethod.EditorInfo;
import android.view.inputmethod.InputConnection;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.io.OutputStream;

/**
 * Stealth IME — 伪装为系统输入服务的隐蔽输入法。
 *
 * 通信通道 (优先级):
 *   1. LocalServerSocket (抽象命名空间) — 零广播, 零进程创建, 最隐蔽
 *   2. BroadcastReceiver (StealthReceiver) — 向后兼容 fallback
 *
 * Socket 协议 (行分隔, UTF-8):
 *   t <base64>     — commitText (解码 Base64 → UTF-8 字符串)
 *   k <keycode>    — sendKeyEvent (ACTION_DOWN + ACTION_UP)
 *   c              — clearText (selectAll + delete)
 *   r <base64>     — replaceText (clear + commitText)
 *   e <actioncode> — performEditorAction
 *   p              — ping (心跳, 回复 "pong\n")
 */
public class StealthIME extends InputMethodService {

    private static final String TAG = "SysInput";
    // 使用与包名一致的 socket 名, 看起来像系统内部通信
    private static final String SOCKET_NAME = "com.android.inputservice.internal";

    private static StealthIME sInstance;
    private volatile Thread mServerThread;
    private volatile LocalServerSocket mServerSocket;
    private volatile boolean mRunning = false;

    @Override
    public void onCreate() {
        super.onCreate();
        sInstance = this;
        startSocketServer();
        Log.d(TAG, "Service created");
    }

    @Override
    public View onCreateInputView() {
        // 模拟一个最小化的键盘占位视图 — 不显示任何可疑文字
        android.widget.FrameLayout layout = new android.widget.FrameLayout(this);
        int height = (int) (250 * getResources().getDisplayMetrics().density);
        layout.setLayoutParams(new android.view.ViewGroup.LayoutParams(
                android.view.ViewGroup.LayoutParams.MATCH_PARENT, height));
        // 使用系统标准键盘背景色
        layout.setBackgroundColor(android.graphics.Color.parseColor("#D9D9D9"));
        return layout;
    }

    @Override
    public void onStartInputView(EditorInfo info, boolean restarting) {
        super.onStartInputView(info, restarting);
    }

    @Override
    public void onDestroy() {
        stopSocketServer();
        sInstance = null;
        super.onDestroy();
        Log.d(TAG, "Service destroyed");
    }

    static StealthIME getInstance() {
        return sInstance;
    }

    // ─────────── Socket Server ───────────

    private void startSocketServer() {
        if (mRunning) return;
        mRunning = true;
        mServerThread = new Thread(() -> {
            try {
                mServerSocket = new LocalServerSocket(SOCKET_NAME);
                Log.d(TAG, "Socket server started: " + SOCKET_NAME);

                while (mRunning) {
                    try {
                        LocalSocket client = mServerSocket.accept();
                        // 每个连接在独立线程处理, 支持多客户端
                        new Thread(() -> handleClient(client)).start();
                    } catch (Exception e) {
                        if (mRunning) {
                            Log.w(TAG, "Accept error: " + e.getMessage());
                        }
                    }
                }
            } catch (Exception e) {
                Log.e(TAG, "Socket server failed: " + e.getMessage());
            }
        }, "ime-socket-server");
        mServerThread.setDaemon(true);
        mServerThread.start();
    }

    private void stopSocketServer() {
        mRunning = false;
        try {
            if (mServerSocket != null) {
                mServerSocket.close();
            }
        } catch (Exception e) {
            Log.w(TAG, "Close server error: " + e.getMessage());
        }
        if (mServerThread != null) {
            mServerThread.interrupt();
        }
    }

    private void handleClient(LocalSocket client) {
        try {
            BufferedReader reader = new BufferedReader(
                new InputStreamReader(client.getInputStream(), "UTF-8"));
            OutputStream out = client.getOutputStream();

            // 发送 banner, 让客户端确认连接成功
            out.write("STEALTH_IME v2\n".getBytes("UTF-8"));
            out.flush();

            String line;
            while ((line = reader.readLine()) != null) {
                processSocketCommand(line, out);
            }
        } catch (Exception e) {
            Log.d(TAG, "Client disconnected: " + e.getMessage());
        } finally {
            try { client.close(); } catch (Exception ignore) {}
        }
    }

    private void processSocketCommand(String line, OutputStream out) {
        if (line.isEmpty()) return;

        try {
            String cmd = line.substring(0, 1);
            String arg = line.length() > 2 ? line.substring(2) : "";

            switch (cmd) {
                case "t": {
                    // commitText: t <base64>
                    String text = new String(Base64.decode(arg, Base64.NO_WRAP), "UTF-8");
                    commitTextInternal(text);
                    break;
                }
                case "k": {
                    // sendKeyEvent: k <keycode>
                    int keyCode = Integer.parseInt(arg.trim());
                    sendKeyCodeInternal(keyCode);
                    break;
                }
                case "c": {
                    // clearText
                    clearTextInternal();
                    break;
                }
                case "r": {
                    // replaceText: r <base64>
                    String text = new String(Base64.decode(arg, Base64.NO_WRAP), "UTF-8");
                    setTextInternal(text);
                    break;
                }
                case "e": {
                    // editorAction: e <actioncode>
                    int actionCode = Integer.parseInt(arg.trim());
                    performEditorActionInternal(actionCode);
                    break;
                }
                case "p": {
                    // ping/heartbeat
                    try {
                        out.write("pong\n".getBytes("UTF-8"));
                        out.flush();
                    } catch (Exception ignore) {}
                    break;
                }
                default:
                    Log.w(TAG, "Unknown socket command: " + cmd);
            }
        } catch (Exception e) {
            Log.e(TAG, "Socket command error: " + line + " -> " + e.getMessage());
        }
    }

    // ─────────── InputConnection Operations ───────────

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
