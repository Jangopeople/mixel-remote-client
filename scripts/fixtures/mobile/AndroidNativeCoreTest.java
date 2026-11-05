package ch.mixel.remote.qa;

import static org.junit.Assert.assertEquals;
import static org.junit.Assert.assertTrue;

import android.content.Context;
import android.os.Build;
import android.system.Os;
import android.system.OsConstants;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import androidx.test.platform.app.InstrumentationRegistry;
import ffi.FFI;
import java.util.Arrays;
import org.junit.Test;
import org.junit.runner.RunWith;

/** Real ARM emulator JNI checks. No Activity, UI input or OS permission grants. */
@RunWith(AndroidJUnit4.class)
public final class AndroidNativeCoreTest {
    @Test
    public void actualKernelPageSize() {
        String expected = InstrumentationRegistry.getArguments().getString("mixelExpectedPageSize");
        assertTrue("The runner must declare the kernel page size", expected != null);
        assertEquals(Long.parseLong(expected), Os.sysconf(OsConstants._SC_PAGESIZE));
    }

    @Test
    public void applicationIdentityAndActualArmRuntime() {
        Context context = InstrumentationRegistry.getInstrumentation().getTargetContext();
        assertEquals("ch.mixel.remote", context.getPackageName());
        assertTrue(Arrays.asList(Build.SUPPORTED_ABIS).contains("arm64-v8a"));
    }

    @Test
    public void actualNativeTranslationAndBrand() {
        // Kotlin's FFI initializer loads the packaged libmixel-remote.so. These
        // methods execute actual exported Rust JNI code, not a Java fake.
        assertEquals("Connect", FFI.INSTANCE.translateLocale("en", "Connect"));
        assertEquals("Mixel-Remote", FFI.INSTANCE.translateLocale("en", "RustDesk"));
    }

    @Test
    public void actualNativeOptionRead() {
        assertEquals("", FFI.INSTANCE.getLocalOption("mixel-qa-nonexistent-native-option-37f9"));
    }
}
