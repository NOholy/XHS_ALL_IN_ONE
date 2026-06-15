#!/bin/bash
set -e

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$DIR"

# Paths to Android SDK tools (update if necessary)
ANDROID_JAR="$HOME/Library/Android/sdk/platforms/android-36/android.jar"
D8="$HOME/Library/Android/sdk/build-tools/37.0.0/d8"

# Optional: Find dynamically if the hardcoded ones don't exist
if [ ! -f "$ANDROID_JAR" ]; then
    ANDROID_JAR=$(find "$HOME/Library/Android/sdk/platforms" -maxdepth 2 -name "android.jar" | sort -r | head -n 1)
fi

if [ ! -f "$D8" ]; then
    D8=$(find "$HOME/Library/Android/sdk/build-tools" -name "d8" | sort -r | head -n 1)
fi

echo "Using ANDROID_JAR: $ANDROID_JAR"
echo "Using D8: $D8"

echo "Compiling TouchInjector.java..."
javac -source 11 -target 11 -cp "$ANDROID_JAR" TouchInjector.java

echo "Converting to dex using d8..."
# Include all generated class files (inner classes: SensorSimulator, Strategy enum)
"$D8" --release --output . TouchInjector*.class

mv classes.dex touch_injector.dex
rm -f TouchInjector*.class

echo "Build successful: touch_injector.dex"
