package com.telcan.rfidsweep;

// Remote debug link (Steve, 2026-10-07; 4.38): "add a connection to the
// app again so you can debug in real time". Off unless Settings >
// DEVELOPER > Remote debugging is on. Every couple of seconds it sends
// the queued log lines (screen changes, camera steps, crash traces from
// last time, main-thread stalls) to /api/devlog/lines and asks
// /api/devlog/commands/next for one command: ping, state, views, logcat,
// shot (a screenshot of the screen that's up, camera picture included),
// camlog, restart_camera.

import android.app.Activity;
import android.content.Context;
import android.content.SharedPreferences;
import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.os.Handler;
import android.os.Looper;
import android.util.Base64;
import android.view.TextureView;
import android.view.View;
import android.view.ViewGroup;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedReader;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.io.PrintWriter;
import java.io.StringWriter;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLEncoder;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;
import java.util.Map;
import java.util.concurrent.ConcurrentLinkedQueue;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

final class DevLink {

    private static final String DEFAULT_SERVER =
            "https://telcan-rfid.azurewebsites.net";
    private static final ConcurrentLinkedQueue<String> queue =
            new ConcurrentLinkedQueue<>();
    private static final Handler main = new Handler(Looper.getMainLooper());
    private static volatile Activity current;
    private static SharedPreferences prefs;
    private static File crashFile;
    private static Thread worker;
    // Uptime, not wall time: it stops while the gun sleeps, so a dark
    // screen isn't reported as a frozen one.
    private static volatile long lastPong = android.os.SystemClock.uptimeMillis();
    private static volatile boolean stallReported;

    private DevLink() {
    }

    /** Once per process (safe to call from every activity). */
    static synchronized void init(Activity a) {
        if (prefs != null) return;
        Context app = a.getApplicationContext();
        prefs = app.getSharedPreferences("sweep", Context.MODE_PRIVATE);
        crashFile = new File(app.getFilesDir(), "last_crash.txt");
        final Thread.UncaughtExceptionHandler prev =
                Thread.getDefaultUncaughtExceptionHandler();
        Thread.setDefaultUncaughtExceptionHandler((t, e) -> {
            try (FileOutputStream out = new FileOutputStream(crashFile)) {
                out.write(("CRASH in thread " + t.getName() + " at "
                        + stamp() + "\n" + trace(e))
                        .getBytes(StandardCharsets.UTF_8));
            } catch (Exception ignored) {
            }
            if (prev != null) prev.uncaughtException(t, e);
        });
        worker = new Thread(DevLink::loop, "devlink");
        worker.setDaemon(true);
        worker.start();
    }

    static boolean enabled() {
        return prefs != null && prefs.getBoolean("dev_remote", false);
    }

    static void log(String tag, String msg) {
        if (!enabled()) return;
        if (queue.size() > 2000) queue.poll();
        queue.add(stamp() + " [" + tag + "] " + msg);
    }

    static void resumed(Activity a) {
        current = a;
        log("screen", a.getClass().getSimpleName() + " resumed");
    }

    static void paused(Activity a) {
        if (current == a) current = null;
        log("screen", a.getClass().getSimpleName() + " paused");
    }

    private static String stamp() {
        return new SimpleDateFormat("HH:mm:ss.SSS", Locale.US)
                .format(new Date());
    }

    static String trace(Throwable e) {
        StringWriter sw = new StringWriter();
        e.printStackTrace(new PrintWriter(sw));
        return sw.toString();
    }

    private static String device() {
        return prefs.getString("device", "C72");
    }

    // ---------------------------------------------------------- loop ----
    private static void loop() {
        while (true) {
            try {
                Thread.sleep(2000);
                if (!enabled()) {
                    // The watchdog only runs while on: don't call the time
                    // it was off a frozen screen.
                    lastPong = android.os.SystemClock.uptimeMillis();
                    continue;
                }
                watchdog();
                sendCrash();
                flush();
                JSONObject r = http("GET", "/api/devlog/commands/next?device="
                        + URLEncoder.encode(device(), "UTF-8"), null);
                JSONObject cmd = r.optJSONObject("command");
                if (cmd != null) run(cmd.getInt("id"), cmd.getString("cmd"));
            } catch (InterruptedException ie) {
                return;
            } catch (Throwable ignored) {
                // Offline or the server hiccuped: try again next round.
            }
        }
    }

    /** The main thread answers a ping every round; if it hasn't for 6 s
     *  the screen is frozen - send what it's stuck on. */
    private static void watchdog() {
        long now = android.os.SystemClock.uptimeMillis();
        if (now - lastPong > 6000 && !stallReported) {
            stallReported = true;
            StringBuilder sb = new StringBuilder("MAIN THREAD STALLED "
                    + (now - lastPong) / 1000 + " s, stuck at:\n");
            for (StackTraceElement el
                    : Looper.getMainLooper().getThread().getStackTrace()) {
                sb.append("  at ").append(el).append('\n');
            }
            log("stall", sb.toString());
        }
        main.post(() -> {
            lastPong = android.os.SystemClock.uptimeMillis();
            stallReported = false;
        });
    }

    private static void sendCrash() throws Exception {
        if (crashFile == null || !crashFile.exists()) return;
        String text = new String(Files.readAllBytes(crashFile.toPath()),
                StandardCharsets.UTF_8);
        http("POST", "/api/devlog/lines", new JSONObject()
                .put("device", device())
                .put("lines", new JSONArray().put(text)));
        crashFile.delete();
    }

    private static void flush() throws Exception {
        if (queue.isEmpty()) return;
        JSONArray lines = new JSONArray();
        String l;
        while (lines.length() < 400 && (l = queue.poll()) != null) lines.put(l);
        http("POST", "/api/devlog/lines", new JSONObject()
                .put("device", device()).put("lines", lines));
    }

    // ------------------------------------------------------ commands ----
    private static void run(int id, String cmd) throws Exception {
        JSONObject res = new JSONObject();
        try {
            switch (cmd) {
                case "ping":
                    res.put("result", "pong " + version() + " screen="
                            + screenName());
                    break;
                case "state":
                    res.put("result", state());
                    break;
                case "views":
                    res.put("result", onMain(() -> {
                        Activity a = current;
                        if (a == null) return "No screen is up.";
                        StringBuilder sb = new StringBuilder();
                        dump(a.getWindow().getDecorView(), 0, sb);
                        return sb.toString();
                    }));
                    break;
                case "logcat":
                    res.put("result", logcat());
                    break;
                case "shot":
                    String[] shot = new String[1];
                    res.put("result", onMain(() -> {
                        Activity a = current;
                        if (a == null) return "No screen is up.";
                        shot[0] = screenshot(a);
                        return "Screenshot of " + screenName();
                    }));
                    if (shot[0] != null) res.put("shot_b64", shot[0]);
                    break;
                case "camlog":
                    res.put("result", onMain(() -> current instanceof CameraActivity
                            ? ((CameraActivity) current).camLogText()
                            : "Box photos isn't the screen that's up ("
                            + screenName() + ")."));
                    break;
                case "restart_camera":
                    res.put("result", onMain(() -> {
                        if (!(current instanceof CameraActivity)) {
                            return "Box photos isn't the screen that's up.";
                        }
                        ((CameraActivity) current).restartCamera();
                        return "Camera restart requested.";
                    }));
                    break;
                default:
                    res.put("result", "Unknown command " + cmd);
            }
        } catch (Throwable t) {
            res.put("result", "Command failed: " + trace(t));
        }
        http("POST", "/api/devlog/commands/" + id + "/result", res);
    }

    private interface MainJob {
        String run() throws Exception;
    }

    /** Run on the main thread and wait (5 s) for the answer. */
    private static String onMain(MainJob job) throws Exception {
        AtomicReference<String> out = new AtomicReference<>();
        CountDownLatch done = new CountDownLatch(1);
        main.post(() -> {
            try {
                out.set(job.run());
            } catch (Throwable t) {
                out.set("Failed: " + trace(t));
            }
            done.countDown();
        });
        if (!done.await(5, TimeUnit.SECONDS)) {
            return "The main thread didn't answer in 5 s (frozen?).";
        }
        return out.get();
    }

    private static String screenName() {
        Activity a = current;
        return a == null ? "none" : a.getClass().getSimpleName();
    }

    private static String version() {
        Activity a = current;
        try {
            Context c = a != null ? a : null;
            if (c == null) return "?";
            android.content.pm.PackageInfo pi = c.getPackageManager()
                    .getPackageInfo(c.getPackageName(), 0);
            return pi.versionName + " (" + pi.versionCode + ")";
        } catch (Exception e) {
            return "?";
        }
    }

    private static String state() {
        StringBuilder sb = new StringBuilder("version " + version()
                + "\nscreen " + screenName() + "\nandroid "
                + android.os.Build.VERSION.RELEASE + " (sdk "
                + android.os.Build.VERSION.SDK_INT + ") "
                + android.os.Build.MANUFACTURER + " " + android.os.Build.MODEL
                + "\n\nprefs:\n");
        for (Map.Entry<String, ?> e : prefs.getAll().entrySet()) {
            String k = e.getKey();
            if (k.equals("key") || k.contains("token") || k.contains("secret")) {
                continue;
            }
            String v = String.valueOf(e.getValue());
            if (v.length() > 120) v = v.substring(0, 120) + "…";
            sb.append(k).append(" = ").append(v).append('\n');
        }
        return sb.toString();
    }

    private static void dump(View v, int depth, StringBuilder sb) {
        if (sb.length() > 60000) return;
        for (int i = 0; i < depth; i++) sb.append("  ");
        sb.append(v.getClass().getSimpleName())
                .append(' ').append(v.getWidth()).append('x').append(v.getHeight())
                .append(v.getVisibility() == View.VISIBLE ? ""
                        : v.getVisibility() == View.GONE ? " GONE" : " INVISIBLE");
        if (v instanceof android.widget.TextView) {
            CharSequence t = ((android.widget.TextView) v).getText();
            if (t != null && t.length() > 0) {
                String s = t.toString().replace('\n', ' ');
                sb.append(" \"").append(s.length() > 60 ? s.substring(0, 60) + "…"
                        : s).append('"');
            }
        }
        if (v instanceof TextureView) {
            sb.append(" available=").append(((TextureView) v).isAvailable());
        }
        sb.append('\n');
        if (v instanceof ViewGroup) {
            ViewGroup g = (ViewGroup) v;
            for (int i = 0; i < g.getChildCount(); i++) {
                dump(g.getChildAt(i), depth + 1, sb);
            }
        }
    }

    private static String logcat() throws Exception {
        Process p = Runtime.getRuntime().exec(
                new String[]{"logcat", "-d", "-t", "500", "-v", "time"});
        StringBuilder sb = new StringBuilder();
        try (BufferedReader r = new BufferedReader(new InputStreamReader(
                p.getInputStream(), StandardCharsets.UTF_8))) {
            String line;
            while ((line = r.readLine()) != null) {
                sb.append(line).append('\n');
                if (sb.length() > 150000) break;
            }
        }
        p.destroy();
        return sb.length() == 0 ? "(logcat returned nothing)" : sb.toString();
    }

    /** The window as a JPEG (base64), with any camera preview painted in
     *  (a TextureView draws black in a plain view snapshot). */
    private static String screenshot(Activity a) {
        View root = a.getWindow().getDecorView();
        int w = root.getWidth(), h = root.getHeight();
        if (w == 0 || h == 0) return null;
        Bitmap bmp = Bitmap.createBitmap(w, h, Bitmap.Config.ARGB_8888);
        Canvas c = new Canvas(bmp);
        root.draw(c);
        // The camera picture only where it shows (a panel can cover it).
        if (!(a instanceof CameraActivity) || !((CameraActivity) a).panelOpen()) {
            paintTextures(root, c);
        }
        float s = Math.min(1f, 720f / w);
        Bitmap small = s < 1f ? Bitmap.createScaledBitmap(bmp,
                Math.round(w * s), Math.round(h * s), true) : bmp;
        ByteArrayOutputStream bos = new ByteArrayOutputStream();
        small.compress(Bitmap.CompressFormat.JPEG, 70, bos);
        if (small != bmp) small.recycle();
        bmp.recycle();
        return Base64.encodeToString(bos.toByteArray(), Base64.NO_WRAP);
    }

    private static void paintTextures(View v, Canvas c) {
        if (v instanceof TextureView && ((TextureView) v).isAvailable()) {
            Bitmap tb = ((TextureView) v).getBitmap();
            if (tb != null) {
                int[] at = new int[2];
                v.getLocationInWindow(at);
                c.drawBitmap(tb, at[0], at[1], null);
                tb.recycle();
            }
        }
        if (v instanceof ViewGroup) {
            ViewGroup g = (ViewGroup) v;
            for (int i = 0; i < g.getChildCount(); i++) {
                paintTextures(g.getChildAt(i), c);
            }
        }
    }

    // ---------------------------------------------------------- http ----
    private static JSONObject http(String method, String path, JSONObject body)
            throws Exception {
        String server = prefs.getString("server", DEFAULT_SERVER)
                .replaceAll("/+$", "");
        HttpURLConnection conn = (HttpURLConnection)
                new URL(server + path).openConnection();
        conn.setConnectTimeout(8000);
        conn.setReadTimeout(20000);
        conn.setRequestMethod(method);
        conn.setRequestProperty("X-Station-Key", prefs.getString("key", ""));
        if (body != null) {
            conn.setRequestProperty("Content-Type", "application/json");
            conn.setDoOutput(true);
            try (OutputStream out = conn.getOutputStream()) {
                out.write(body.toString().getBytes(StandardCharsets.UTF_8));
            }
        }
        int code = conn.getResponseCode();
        InputStream in = code >= 400 ? conn.getErrorStream()
                : conn.getInputStream();
        StringBuilder sb = new StringBuilder();
        if (in != null) {
            try (BufferedReader r = new BufferedReader(new InputStreamReader(
                    in, StandardCharsets.UTF_8))) {
                String line;
                while ((line = r.readLine()) != null) sb.append(line);
            }
        }
        conn.disconnect();
        if (code >= 400) throw new Exception("HTTP " + code);
        return sb.length() == 0 ? new JSONObject() : new JSONObject(sb.toString());
    }
}
