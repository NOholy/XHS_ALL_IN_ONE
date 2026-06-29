import android.hardware.input.InputManager;
import android.os.SystemClock;
import android.view.InputDevice;
import android.view.InputEvent;
import android.view.KeyEvent;
import android.view.MotionEvent;
import android.view.MotionEvent.PointerCoords;
import android.view.MotionEvent.PointerProperties;
import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.lang.reflect.Constructor;
import java.lang.reflect.Method;
import android.net.LocalServerSocket;
import android.net.LocalSocket;
import java.util.HashMap;
import java.util.Map;
import java.util.LinkedHashMap;
import java.util.Random;

public class SensorHalService {
    private static Method injectInputEventMethod;
    private static Object inputManager;
    private static long downTime = 0;
    
    // Map to keep track of active pointers (LinkedHashMap to preserve order)
    private static Map<Integer, PointerCoords> activeCoords = new LinkedHashMap<>();
    private static Map<Integer, PointerProperties> activeProps = new LinkedHashMap<>();
    
    // Touch buffers
    private static Map<Integer, PointerCoords> pendingCoords = new LinkedHashMap<>();
    private static Map<Integer, PointerProperties> pendingProps = new LinkedHashMap<>();
    private static Map<Integer, Boolean> pendingUp = new LinkedHashMap<>();
    private static Map<Integer, Boolean> pendingDown = new LinkedHashMap<>();

    // Sensor simulator instance (null if disabled)
    private static SensorSimulator sensorSim;
    private static String sensorMode = "off";
    
    // Discovered Touchscreen Device ID
    private static int touchDeviceId = 0;

    public static void main(String[] args) {
        int maxX = 1080;
        int maxY = 2400;
        String socketName = "touch_injector";
        
        if (args.length >= 2) {
            maxX = Integer.parseInt(args[0]);
            maxY = Integer.parseInt(args[1]);
        }
        if (args.length >= 3) {
            socketName = args[2];
        }
        if (args.length >= 4) {
            sensorMode = args[3];
        }

        try {
            Class<?> inputManagerClass = Class.forName("android.hardware.input.InputManager");
            Method getInstanceMethod = inputManagerClass.getDeclaredMethod("getInstance");
            inputManager = getInstanceMethod.invoke(null);
            injectInputEventMethod = inputManagerClass.getMethod("injectInputEvent", InputEvent.class, int.class);
            
            // Auto-detect real touchscreen device ID
            int[] deviceIds = InputDevice.getDeviceIds();
            for (int id : deviceIds) {
                InputDevice device = InputDevice.getDevice(id);
                if (device != null && (device.getSources() & InputDevice.SOURCE_TOUCHSCREEN) == InputDevice.SOURCE_TOUCHSCREEN) {
                    if (!device.isVirtual()) {
                        touchDeviceId = id;
                        break;
                    }
                }
            }
            // Fallback to first available if no non-virtual found
            if (touchDeviceId == 0 && deviceIds.length > 0) {
                for (int id : deviceIds) {
                    InputDevice device = InputDevice.getDevice(id);
                    if (device != null && (device.getSources() & InputDevice.SOURCE_TOUCHSCREEN) == InputDevice.SOURCE_TOUCHSCREEN) {
                        touchDeviceId = id;
                        break;
                    }
                }
            }
            
            System.out.println("InputManager initialized. MaxX=" + maxX + " MaxY=" + maxY + " touchDeviceId=" + touchDeviceId);

            // ── Initialize Sensor Simulator ──
            if (!"off".equals(sensorMode)) {
                sensorSim = new SensorSimulator(sensorMode);
                sensorSim.start();
                // Wait briefly for initialization to complete
                Thread.sleep(500);
                System.out.println("[SensorSim] Started. mode=" + sensorMode
                    + " strategy=" + sensorSim.getStrategyName()
                    + " active=" + sensorSim.isActive());
            } else {
                System.out.println("[SensorSim] Disabled (mode=off).");
            }
            
            try (LocalServerSocket serverSocket = new LocalServerSocket(socketName)) {
                System.out.println("Listening on local socket " + socketName);
                while (true) {
                    LocalSocket clientSocket = serverSocket.accept();
                    handleClient(clientSocket, maxX, maxY);
                }
            }
        } catch (Exception e) {
            e.printStackTrace();
        }
    }

    private static void handleClient(LocalSocket socket, int maxX, int maxY) {
        try {
            // Build banner with sensor status line
            String sensorLine = "";
            if (sensorSim != null) {
                sensorLine = "s " + sensorMode + " " + sensorSim.getStrategyName()
                    + " " + (sensorSim.isActive() ? "active" : "inactive") + "\n";
            } else {
                sensorLine = "s off none inactive\n";
            }
            String banner = "v 1\n^ 10 " + maxX + " " + maxY + " 255\n"
                + sensorLine + "$ " + android.os.Process.myPid() + "\n";
            socket.getOutputStream().write(banner.getBytes());
            socket.getOutputStream().flush();

            BufferedReader reader = new BufferedReader(new InputStreamReader(socket.getInputStream()));
            String line;
            while ((line = reader.readLine()) != null) {
                processCommand(line);
            }
        } catch (Exception e) {
            e.printStackTrace();
        } finally {
            try { socket.close(); } catch (Exception e) {}
            // Clean up all touches on disconnect to prevent stuck touches
            if (!activeCoords.isEmpty()) {
                long eventTime = SystemClock.uptimeMillis();
                sendEvent(MotionEvent.ACTION_CANCEL, eventTime, -1);
            }
            activeCoords.clear();
            activeProps.clear();
            pendingCoords.clear();
            pendingProps.clear();
            pendingUp.clear();
            pendingDown.clear();
        }
    }

    private static void processCommand(String line) {
        if (line.length() == 0) return;
        String[] parts = line.split(" ");
        String cmd = parts[0];

        try {
            if (cmd.equals("d")) {
                int contact = Integer.parseInt(parts[1]);
                float x = Float.parseFloat(parts[2]);
                float y = Float.parseFloat(parts[3]);
                float pressure = Float.parseFloat(parts[4]) / 255.0f;
                float size = 0.04f + (pressure * 0.06f); // dynamic size between 0.04 and 0.1
                
                PointerCoords c = new PointerCoords();
                c.x = x; c.y = y; c.pressure = pressure; c.size = size;
                c.touchMajor = size * 100f; c.touchMinor = size * 80f;
                PointerProperties p = new PointerProperties();
                p.id = contact; p.toolType = MotionEvent.TOOL_TYPE_FINGER;
                
                pendingCoords.put(contact, c);
                pendingProps.put(contact, p);
                pendingDown.put(contact, true);

                // Notify sensor simulator of touch down
                if (sensorSim != null) {
                    sensorSim.notifyTouchEvent("down", x, y, pressure);
                }
                
            } else if (cmd.equals("m")) {
                int contact = Integer.parseInt(parts[1]);
                float x = Float.parseFloat(parts[2]);
                float y = Float.parseFloat(parts[3]);
                float pressure = Float.parseFloat(parts[4]) / 255.0f;
                float size = 0.04f + (pressure * 0.06f);
                
                PointerCoords c = new PointerCoords();
                c.x = x; c.y = y; c.pressure = pressure; c.size = size;
                c.touchMajor = size * 100f; c.touchMinor = size * 80f;
                PointerProperties p = new PointerProperties();
                p.id = contact; p.toolType = MotionEvent.TOOL_TYPE_FINGER;
                
                pendingCoords.put(contact, c);
                pendingProps.put(contact, p);

                // Notify sensor simulator of touch move
                if (sensorSim != null) {
                    sensorSim.notifyTouchEvent("move", x, y, pressure);
                }
                
            } else if (cmd.equals("u")) {
                int contact = Integer.parseInt(parts[1]);
                pendingUp.put(contact, true);

                // Notify sensor simulator of touch up
                if (sensorSim != null) {
                    sensorSim.notifyTouchEvent("up", 0, 0, 0);
                }
                
            } else if (cmd.equals("c")) {
                commit();
            } else if (cmd.equals("k")) {
                // KeyEvent injection: "k <keycode>" — replaces adb shell input keyevent
                int keyCode = Integer.parseInt(parts[1]);
                injectKeyEvent(keyCode);
            }
        } catch (Exception e) {
            System.err.println("Error processing line: " + line);
            e.printStackTrace();
        }
    }

    private static void commit() {
        long eventTime = SystemClock.uptimeMillis();
        
        // 1. Process Downs
        for (Map.Entry<Integer, Boolean> entry : pendingDown.entrySet()) {
            int contact = entry.getKey();
            activeCoords.put(contact, pendingCoords.get(contact));
            activeProps.put(contact, pendingProps.get(contact));
            
            if (activeCoords.size() == 1) {
                downTime = eventTime;
                sendEvent(MotionEvent.ACTION_DOWN, eventTime, contact);
            } else {
                int action = MotionEvent.ACTION_POINTER_DOWN | (getIndex(contact) << MotionEvent.ACTION_POINTER_INDEX_SHIFT);
                sendEvent(action, eventTime, contact);
            }
        }
        pendingDown.clear();
        
        // 2. Process Moves
        boolean hasMove = false;
        for (Map.Entry<Integer, PointerCoords> entry : pendingCoords.entrySet()) {
            int contact = entry.getKey();
            if (activeCoords.containsKey(contact)) {
                activeCoords.put(contact, entry.getValue());
                hasMove = true;
            }
        }
        if (hasMove && pendingUp.isEmpty()) { // Only send pure move if no UP in this commit
            sendEvent(MotionEvent.ACTION_MOVE, eventTime, -1);
        }
        pendingCoords.clear();
        pendingProps.clear();

        // 3. Process Ups
        for (Map.Entry<Integer, Boolean> entry : pendingUp.entrySet()) {
            int contact = entry.getKey();
            if (!activeCoords.containsKey(contact)) continue;
            
            if (activeCoords.size() == 1) {
                sendEvent(MotionEvent.ACTION_UP, eventTime, contact);
                activeCoords.remove(contact);
                activeProps.remove(contact);
            } else {
                int action = MotionEvent.ACTION_POINTER_UP | (getIndex(contact) << MotionEvent.ACTION_POINTER_INDEX_SHIFT);
                sendEvent(action, eventTime, contact);
                activeCoords.remove(contact);
                activeProps.remove(contact);
            }
        }
        pendingUp.clear();
    }

    private static int getIndex(int contactId) {
        int idx = 0;
        for (Integer id : activeProps.keySet()) {
            if (id == contactId) return idx;
            idx++;
        }
        return 0;
    }

    private static void sendEvent(int action, long eventTime, int actionPointerId) {
        int count = activeProps.size();
        if (count == 0) return;
        
        PointerProperties[] props = new PointerProperties[count];
        PointerCoords[] coords = new PointerCoords[count];
        
        int i = 0;
        for (Integer id : activeProps.keySet()) {
            props[i] = activeProps.get(id);
            coords[i] = activeCoords.get(id);
            i++;
        }

        MotionEvent event = MotionEvent.obtain(
                downTime, eventTime, action, count,
                props, coords, 0, 0, 1.0f, 1.0f,
                touchDeviceId, 0, InputDevice.SOURCE_TOUCHSCREEN, 0);

        try {
            // INJECT_INPUT_EVENT_MODE_ASYNC = 0
            injectInputEventMethod.invoke(inputManager, event, 0);
        } catch (Exception e) {
            e.printStackTrace();
        } finally {
            event.recycle();
        }
    }

    /**
     * Inject a KeyEvent (DOWN + UP) through InputManager.
     * Uses SOURCE_KEYBOARD so the event appears to come from a real hardware keyboard,
     * unlike 'adb shell input keyevent' which sets deviceId=-1 (virtual/synthetic).
     * This is critical to avoid risk control detection of synthetic key presses.
     */
    private static void injectKeyEvent(int keyCode) {
        long now = SystemClock.uptimeMillis();
        try {
            // ACTION_DOWN
            KeyEvent downEvent = new KeyEvent(
                now, now, KeyEvent.ACTION_DOWN, keyCode, 0, 0,
                -1, 0, 0, InputDevice.SOURCE_KEYBOARD
            );
            injectInputEventMethod.invoke(inputManager, downEvent, 0);

            // Brief delay between down and up (mimics real key press duration)
            Thread.sleep(2 + new Random().nextInt(8)); // 2-10ms

            long upTime = SystemClock.uptimeMillis();
            // ACTION_UP
            KeyEvent upEvent = new KeyEvent(
                now, upTime, KeyEvent.ACTION_UP, keyCode, 0, 0,
                -1, 0, 0, InputDevice.SOURCE_KEYBOARD
            );
            injectInputEventMethod.invoke(inputManager, upEvent, 0);
        } catch (Exception e) {
            System.err.println("KeyEvent injection failed for keyCode=" + keyCode);
            e.printStackTrace();
        }
    }

    // ═══════════════════════════════════════════════════════════════════
    //  SensorSimulator — IMU data injection correlated with touch events
    // ═══════════════════════════════════════════════════════════════════

    static class SensorSimulator extends Thread {

        // ── Injection strategies ──
        enum Strategy { FRAMEWORK, VIBRATOR, NONE }

        private final String mode; // "coupled" or "always_on"
        private volatile boolean running = true;
        private Strategy strategy = Strategy.NONE;

        // ── Framework injection state ──
        private Object sensorManager;
        private Object accelSensor;
        private Object gyroSensor;
        private Method injectDataMethod;

        // ── Vibrator fallback state ──
        private Object vibrator;
        private boolean hasAmplitudeControl = false;
        private Method vibrateMethod;        // Vibrator.vibrate(VibrationEffect) or Vibrator.vibrate(long)
        private boolean useVibrationEffect = false;

        // ── System context ──
        private Object systemContext;

        // ── Touch state (cross-thread, updated by main thread) ──
        private volatile String touchAction = "idle"; // "idle", "down", "move", "up"
        private volatile float touchX = 0, touchY = 0;
        private volatile float prevTouchX = 0, prevTouchY = 0;
        private volatile float touchPressure = 0;
        private volatile long touchTimeMs = 0;

        // ── Physics model ──
        private final Random rng = new Random();
        private final long startTimeMs = System.currentTimeMillis();

        // Hand-hold tilt offsets (randomized per session for uniqueness)
        private final float tiltX = 0.15f + (float)(Math.random() * 0.15);
        private final float tiltY = 0.10f + (float)(Math.random() * 0.12);

        // Accelerometer constants (m/s²)
        private static final float ACCEL_BREATHING_AMP = 0.04f;
        private static final float ACCEL_BREATHING_FREQ = 0.25f;   // ~15 breaths/min
        private static final float ACCEL_NOISE_SIGMA = 0.015f;
        private static final float ACCEL_TAP_IMPULSE_MIN = 0.3f;
        private static final float ACCEL_TAP_IMPULSE_MAX = 0.8f;
        private static final float ACCEL_SWIPE_COUPLING = 0.00015f;

        // Gyroscope constants (rad/s)
        private static final float GYRO_BREATHING_AMP = 0.006f;
        private static final float GYRO_BREATHING_FREQ = 0.25f;
        private static final float GYRO_NOISE_SIGMA = 0.002f;
        private static final float GYRO_TAP_IMPULSE_MIN = 0.01f;
        private static final float GYRO_TAP_IMPULSE_MAX = 0.04f;
        private static final float GYRO_SWIPE_COUPLING = 0.00002f;

        // Vibrator timing
        private long lastVibrateMs = 0;

        SensorSimulator(String mode) {
            super("SensorSimulator");
            this.mode = mode;
            setDaemon(true);
        }

        /**
         * Called from main thread when a touch event occurs.
         */
        void notifyTouchEvent(String action, float x, float y, float pressure) {
            this.prevTouchX = this.touchX;
            this.prevTouchY = this.touchY;
            this.touchAction = action;
            this.touchX = x;
            this.touchY = y;
            this.touchPressure = pressure;
            this.touchTimeMs = System.currentTimeMillis();
        }

        String getStrategyName() {
            switch (strategy) {
                case FRAMEWORK: return "framework";
                case VIBRATOR:  return "vibrator";
                default:        return "none";
            }
        }

        boolean isActive() {
            return strategy != Strategy.NONE && running;
        }

        @Override
        public void run() {
            System.out.println("[SensorSim] Initializing...");

            // Try to obtain system context first (needed by all strategies)
            try {
                initSystemContext();
            } catch (Exception e) {
                System.err.println("[SensorSim] Context init failed: " + e.getMessage());
            }

            // ── Strategy 1: Framework SensorManager injection ──
            if (systemContext != null) {
                try {
                    initFrameworkInjection();
                } catch (Exception e) {
                    System.err.println("[SensorSim] Strategy FRAMEWORK failed: " + e.getMessage());
                }
            }

            // ── Strategy 2: Vibrator micro-pulse fallback ──
            if (strategy == Strategy.NONE && systemContext != null) {
                try {
                    initVibratorFallback();
                } catch (Exception e) {
                    System.err.println("[SensorSim] Strategy VIBRATOR failed: " + e.getMessage());
                }
            }

            // ── Strategy 3: None ──
            if (strategy == Strategy.NONE) {
                System.err.println("[SensorSim] WARNING: No injection strategy available. "
                    + "Sensor data will remain static. Consider rooting or using LSPosed.");
            }

            System.out.println("[SensorSim] Active strategy: " + getStrategyName());

            // ── Main sensor generation loop ──
            while (running) {
                try {
                    long now = System.currentTimeMillis();

                    if (strategy == Strategy.FRAMEWORK) {
                        // 50Hz framework injection
                        float[] accelData = generateAccelData(now);
                        float[] gyroData = generateGyroData(now);
                        injectFrameworkData(accelSensor, accelData);
                        injectFrameworkData(gyroSensor, gyroData);
                        Thread.sleep(20);

                    } else if (strategy == Strategy.VIBRATOR) {
                        // Vibrator micro-pulse at variable intervals
                        long sinceTouchMs = now - touchTimeMs;
                        boolean touchActive = sinceTouchMs < 2000;
                        long interval;

                        if (touchActive) {
                            // More frequent during touch interaction
                            interval = 500 + rng.nextInt(1000); // 0.5–1.5s
                        } else {
                            // Idle breathing rhythm: occasional micro-pulses
                            interval = 2000 + rng.nextInt(6000); // 2–8s
                        }

                        if (now - lastVibrateMs >= interval) {
                            doMicroVibrate(touchActive);
                            lastVibrateMs = now;
                        }
                        Thread.sleep(100); // Check every 100ms

                    } else {
                        // No strategy — just sleep to avoid busy loop
                        Thread.sleep(1000);
                    }
                } catch (InterruptedException e) {
                    break;
                } catch (Exception e) {
                    // Never crash — log and continue
                    System.err.println("[SensorSim] Loop error: " + e.getMessage());
                    try { Thread.sleep(100); } catch (InterruptedException ie) { break; }
                }
            }
        }

        // ═══════════════════════════════════════════
        //  Context Initialization
        // ═══════════════════════════════════════════

        private void initSystemContext() throws Exception {
            // Use ActivityThread.systemMain() to get a system Context
            // This works in app_process because we run as shell user
            Class<?> looperClass = Class.forName("android.os.Looper");
            try {
                Method prepareMainLooper = looperClass.getDeclaredMethod("prepareMainLooper");
                prepareMainLooper.invoke(null);
            } catch (Exception e) {
                // Looper might already be prepared — that's fine
                System.err.println("[SensorSim] Looper.prepareMainLooper: " + e.getMessage());
                // Try Looper.prepare() as fallback
                try {
                    Method prepare = looperClass.getDeclaredMethod("prepare");
                    prepare.invoke(null);
                } catch (Exception e2) {
                    // Already has a looper, continue
                }
            }

            Class<?> atClass = Class.forName("android.app.ActivityThread");
            Method systemMain = atClass.getDeclaredMethod("systemMain");
            systemMain.setAccessible(true);
            Object activityThread = systemMain.invoke(null);

            Method getSystemContext = atClass.getDeclaredMethod("getSystemContext");
            getSystemContext.setAccessible(true);
            systemContext = getSystemContext.invoke(activityThread);

            System.out.println("[SensorSim] System context obtained.");
        }

        // ═══════════════════════════════════════════
        //  Strategy 1: Framework SensorManager
        // ═══════════════════════════════════════════

        private void initFrameworkInjection() throws Exception {
            // Get SensorManager from context
            Method getSystemService = systemContext.getClass().getMethod("getSystemService", String.class);
            sensorManager = getSystemService.invoke(systemContext, "sensor");
            if (sensorManager == null) {
                throw new Exception("SensorManager is null");
            }

            // Get accelerometer sensor (TYPE_ACCELEROMETER = 1)
            Method getDefaultSensor = sensorManager.getClass().getMethod("getDefaultSensor", int.class);
            accelSensor = getDefaultSensor.invoke(sensorManager, 1);
            gyroSensor = getDefaultSensor.invoke(sensorManager, 4); // TYPE_GYROSCOPE = 4

            if (accelSensor == null) {
                throw new Exception("No accelerometer sensor found on device");
            }

            // Try to enable data injection mode (hidden API)
            Method initDataInjection = null;
            try {
                initDataInjection = sensorManager.getClass().getDeclaredMethod("initDataInjection", boolean.class);
                initDataInjection.setAccessible(true);
            } catch (NoSuchMethodException e) {
                throw new Exception("initDataInjection not found (API < 24?)");
            }

            boolean injectionEnabled = (Boolean) initDataInjection.invoke(sensorManager, true);
            if (!injectionEnabled) {
                throw new Exception("initDataInjection returned false (sensors don't support injection flag)");
            }

            // Get the injectSensorData method
            Class<?> sensorClass = Class.forName("android.hardware.Sensor");
            injectDataMethod = sensorManager.getClass().getDeclaredMethod(
                "injectSensorData", sensorClass, float[].class, int.class, long.class
            );
            injectDataMethod.setAccessible(true);

            strategy = Strategy.FRAMEWORK;
            System.out.println("[SensorSim] FRAMEWORK strategy active. "
                + "Accel=" + (accelSensor != null) + " Gyro=" + (gyroSensor != null));
        }

        private void injectFrameworkData(Object sensor, float[] values) {
            if (sensor == null || injectDataMethod == null) return;
            try {
                // SensorManager.SENSOR_STATUS_ACCURACY_HIGH = 3
                injectDataMethod.invoke(sensorManager, sensor, values, 3, System.nanoTime());
            } catch (Exception e) {
                // Silently ignore — don't spam logs
            }
        }

        // ═══════════════════════════════════════════
        //  Strategy 2: Vibrator Micro-Pulse
        // ═══════════════════════════════════════════

        private void initVibratorFallback() throws Exception {
            Method getSystemService = systemContext.getClass().getMethod("getSystemService", String.class);
            vibrator = getSystemService.invoke(systemContext, "vibrator");
            if (vibrator == null) {
                throw new Exception("Vibrator service is null");
            }

            // Check for VibrationEffect support (API 26+)
            try {
                Method hasAmpCtrl = vibrator.getClass().getMethod("hasAmplitudeControl");
                hasAmplitudeControl = (Boolean) hasAmpCtrl.invoke(vibrator);
            } catch (NoSuchMethodException e) {
                hasAmplitudeControl = false;
            }

            // Resolve vibrate method
            if (hasAmplitudeControl) {
                // Use VibrationEffect.createOneShot(long milliseconds, int amplitude)
                useVibrationEffect = true;
                try {
                    Class<?> veClass = Class.forName("android.os.VibrationEffect");
                    vibrateMethod = vibrator.getClass().getMethod("vibrate", veClass);
                } catch (Exception e) {
                    useVibrationEffect = false;
                }
            }

            if (!useVibrationEffect) {
                // Fallback: Vibrator.vibrate(long milliseconds)
                vibrateMethod = vibrator.getClass().getMethod("vibrate", long.class);
            }

            strategy = Strategy.VIBRATOR;
            System.out.println("[SensorSim] VIBRATOR strategy active. "
                + "amplitudeControl=" + hasAmplitudeControl);
        }

        private void doMicroVibrate(boolean touchActive) {
            try {
                int durationMs = 3 + rng.nextInt(6); // 3–8ms
                if (useVibrationEffect && hasAmplitudeControl) {
                    // Low amplitude: 1–10 on 0–255 scale
                    int amplitude = touchActive ? (5 + rng.nextInt(8)) : (1 + rng.nextInt(5));
                    Class<?> veClass = Class.forName("android.os.VibrationEffect");
                    Method createOneShot = veClass.getMethod("createOneShot", long.class, int.class);
                    Object effect = createOneShot.invoke(null, (long) durationMs, amplitude);
                    vibrateMethod.invoke(vibrator, effect);
                } else if (vibrateMethod != null) {
                    // No amplitude control — just very short pulses
                    vibrateMethod.invoke(vibrator, (long) durationMs);
                }
            } catch (Exception e) {
                // Silently ignore vibrator errors
            }
        }

        // ═══════════════════════════════════════════
        //  Physics-Based Sensor Data Generation
        // ═══════════════════════════════════════════

        /**
         * Generate realistic accelerometer data (m/s², 3-axis).
         * Models: gravity + hand-hold tilt + breathing + noise + touch coupling
         */
        private float[] generateAccelData(long now) {
            float elapsed = (now - startTimeMs) / 1000.0f;
            long sinceTouchMs = now - touchTimeMs;

            // ── Base: gravity with hand-hold tilt ──
            float ax = tiltX + 0.05f * (float) Math.sin(elapsed * 0.03 * 2 * Math.PI);
            float ay = tiltY + 0.04f * (float) Math.cos(elapsed * 0.04 * 2 * Math.PI);
            float az = 9.81f - 0.02f * (float) Math.sin(elapsed * 0.02 * 2 * Math.PI);

            // ── Breathing oscillation (~0.25Hz) ──
            float breathPhase = (float) (2 * Math.PI * ACCEL_BREATHING_FREQ * elapsed);
            ax += ACCEL_BREATHING_AMP * (float) Math.sin(breathPhase);
            ay += ACCEL_BREATHING_AMP * 0.6f * (float) Math.sin(breathPhase + 0.7f);
            az += ACCEL_BREATHING_AMP * 0.3f * (float) Math.cos(breathPhase);

            // ── White noise (Gaussian) ──
            ax += (float) (rng.nextGaussian() * ACCEL_NOISE_SIGMA);
            ay += (float) (rng.nextGaussian() * ACCEL_NOISE_SIGMA);
            az += (float) (rng.nextGaussian() * ACCEL_NOISE_SIGMA * 0.8);

            // ── Touch-correlated perturbation ──
            String action = touchAction; // snapshot volatile
            if (sinceTouchMs < 500 && !"idle".equals(action)) {
                float decay = 1.0f - (sinceTouchMs / 500.0f);

                if ("down".equals(action)) {
                    // Impact pulse — brief spike when finger lands
                    float impulse = ACCEL_TAP_IMPULSE_MIN
                        + rng.nextFloat() * (ACCEL_TAP_IMPULSE_MAX - ACCEL_TAP_IMPULSE_MIN);
                    float impactDecay = (float) Math.exp(-sinceTouchMs / 80.0);
                    ax += impulse * impactDecay * (0.5f + (float) rng.nextGaussian() * 0.3f);
                    ay += impulse * impactDecay * (0.3f + (float) rng.nextGaussian() * 0.2f);
                    az -= impulse * impactDecay * 0.4f; // push down

                } else if ("move".equals(action)) {
                    // Swipe coupling — proportional to displacement
                    float dx = touchX - prevTouchX;
                    float dy = touchY - prevTouchY;
                    ax += dx * ACCEL_SWIPE_COUPLING * decay;
                    ay += dy * ACCEL_SWIPE_COUPLING * decay;

                } else if ("up".equals(action)) {
                    // Rebound — damped sinusoidal oscillation
                    float rebound = ACCEL_TAP_IMPULSE_MIN * 0.6f
                        + rng.nextFloat() * ACCEL_TAP_IMPULSE_MIN * 0.4f;
                    float dampedOsc = (float) Math.exp(-sinceTouchMs / 120.0)
                        * (float) Math.sin(sinceTouchMs / 25.0);
                    ax += rebound * dampedOsc;
                    ay += rebound * dampedOsc * 0.7f;
                    az += rebound * dampedOsc * 0.3f;
                }
            }

            return new float[]{ax, ay, az};
        }

        /**
         * Generate realistic gyroscope data (rad/s, 3-axis).
         * Models: breathing + noise + touch angular coupling
         */
        private float[] generateGyroData(long now) {
            float elapsed = (now - startTimeMs) / 1000.0f;
            long sinceTouchMs = now - touchTimeMs;

            // ── Breathing oscillation ──
            float breathPhase = (float) (2 * Math.PI * GYRO_BREATHING_FREQ * elapsed);
            float gx = GYRO_BREATHING_AMP * (float) Math.sin(breathPhase + 0.3f);
            float gy = GYRO_BREATHING_AMP * 0.7f * (float) Math.cos(breathPhase + 1.1f);
            float gz = GYRO_BREATHING_AMP * 0.4f * (float) Math.sin(breathPhase * 0.8f + 2.0f);

            // ── White noise ──
            gx += (float) (rng.nextGaussian() * GYRO_NOISE_SIGMA);
            gy += (float) (rng.nextGaussian() * GYRO_NOISE_SIGMA);
            gz += (float) (rng.nextGaussian() * GYRO_NOISE_SIGMA * 0.6);

            // ── Touch-correlated angular perturbation ──
            String action = touchAction;
            if (sinceTouchMs < 400 && !"idle".equals(action)) {
                float decay = 1.0f - (sinceTouchMs / 400.0f);

                if ("down".equals(action)) {
                    float impulse = GYRO_TAP_IMPULSE_MIN
                        + rng.nextFloat() * (GYRO_TAP_IMPULSE_MAX - GYRO_TAP_IMPULSE_MIN);
                    float impactDecay = (float) Math.exp(-sinceTouchMs / 60.0);
                    gx += impulse * impactDecay * (float) rng.nextGaussian();
                    gy += impulse * impactDecay * (float) rng.nextGaussian() * 0.8f;

                } else if ("move".equals(action)) {
                    float dx = touchX - prevTouchX;
                    float dy = touchY - prevTouchY;
                    // Wrist rotation follows swipe direction
                    gx += dy * GYRO_SWIPE_COUPLING * decay;
                    gy -= dx * GYRO_SWIPE_COUPLING * decay;

                } else if ("up".equals(action)) {
                    float rebound = GYRO_TAP_IMPULSE_MIN * 0.5f;
                    float dampedOsc = (float) Math.exp(-sinceTouchMs / 80.0)
                        * (float) Math.sin(sinceTouchMs / 20.0);
                    gx += rebound * dampedOsc;
                    gy += rebound * dampedOsc * 0.6f;
                }
            }

            return new float[]{gx, gy, gz};
        }
    }
}
