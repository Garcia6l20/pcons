// SPDX-License-Identifier: MIT
package org.pcons.qtmultiabi;

import android.os.Build;

public class AppInfo {
    public static String describe() {
        return "Java says: API " + Build.VERSION.SDK_INT + " on " + Build.MODEL;
    }
}
