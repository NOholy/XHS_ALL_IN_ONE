# ============================================================
# Stealth IME ProGuard Rules
# ============================================================
# 保留 InputMethodService 子类（Android 系统通过反射加载）
-keep public class com.android.inputservice.settings.StealthIME {
    public *;
    protected *;
}

# 除了上述必须保留的入口点，其余所有类/方法/字段名全部混淆
-dontwarn android.**
-dontwarn java.**

# 移除 Log 调用（Release 包中不留任何调试日志，防止 logcat 泄露信息）
-assumenosideeffects class android.util.Log {
    public static boolean isLoggable(java.lang.String, int);
    public static int v(...);
    public static int d(...);
    public static int i(...);
    public static int w(...);
    public static int e(...);
}
