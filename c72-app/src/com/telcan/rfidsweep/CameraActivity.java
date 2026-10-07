package com.telcan.rfidsweep;

// Box photos collector (Steve, 2026-10-07; 4.36). A developer-mode camera
// with a built-in file sorter: photograph Svbony boxes (no barcodes, just
// the SKU in plain text), the server reads each photo with Azure and files
// it into a SKU folder, and the shipment's confirmed boxes go to the web
// sorter. The photos are the training set for the future live SKU reader.
//
// Screens: the camera, Incoming (photos that need a tap), Folders, one
// folder, plus the folder picker and the Send to sorter review. The
// server is the source of truth for photos; the gun only keeps an upload
// queue (files/boxq) until each photo is safely on the server.

import android.Manifest;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.Context;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.ImageFormat;
import android.graphics.Matrix;
import android.graphics.Paint;
import android.graphics.SurfaceTexture;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.hardware.camera2.CameraAccessException;
import android.hardware.camera2.CameraCaptureSession;
import android.hardware.camera2.CameraCharacteristics;
import android.hardware.camera2.CameraDevice;
import android.hardware.camera2.CameraManager;
import android.hardware.camera2.CameraMetadata;
import android.hardware.camera2.CaptureRequest;
import android.hardware.camera2.params.StreamConfigurationMap;
import android.media.ExifInterface;
import android.media.Image;
import android.media.ImageReader;
import android.media.MediaActionSound;
import android.os.Bundle;
import android.os.Handler;
import android.os.HandlerThread;
import android.os.Looper;
import android.text.Editable;
import android.text.TextWatcher;
import android.util.Base64;
import android.util.LruCache;
import android.util.Size;
import android.view.Gravity;
import android.view.KeyEvent;
import android.view.Surface;
import android.view.TextureView;
import android.view.View;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.GridLayout;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.ProgressBar;
import android.widget.ScrollView;
import android.widget.TextView;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.net.URLEncoder;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Date;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Random;
import java.util.Set;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public class CameraActivity extends Activity {

    private static final String DEFAULT_SERVER =
            "https://telcan-rfid.azurewebsites.net";
    // Same trigger keys as the main screen.
    private static final int[] TRIGGER_KEYS = {
            139, 280, 291, 293, 294, 311, 312, 313, 315, 591, 593, 594, 595, 596
    };
    private static final int REQ_CAMERA = 41;

    // The gun's dark palette (MainActivity's DEF_*_DARK slots).
    private static final int BG = 0xFF16181A;
    private static final int CARD = 0xFF202224;
    private static final int CHIP = 0xFF2D2F31;
    private static final int LINE = 0xFF34363A;
    private static final int TEXT = 0xFFE6E8EA;
    private static final int MUTED = 0xFF9BA0A5;
    private static final int BLUE = 0xFF2F7DE1;
    private static final int BLUE_FILL = 0xFF2A6FCC;
    private static final int BLUE_TEXT = 0xFF8FB8EE;
    private static final int SOFT = 0xFF1C2E46;
    private static final int OK = 0xFF35A273;
    private static final int OK_BG = 0xFF1D362E;
    private static final int OK_TEXT = 0xFF7FD3AE;
    private static final int WARN = 0xFFD9B25C;
    private static final int WARN_BG = 0xFF413A29;
    private static final int BAD_TEXT = 0xFFF08A79;
    private static final int BAD_LINE = 0xFF5A2E28;
    private static final int KRAFT = 0xFFB0844F;

    private SharedPreferences prefs;
    private final Handler ui = new Handler(Looper.getMainLooper());
    private final ExecutorService uploadExec = Executors.newSingleThreadExecutor();
    private final ExecutorService netExec = Executors.newSingleThreadExecutor();
    private final ExecutorService thumbExec = Executors.newFixedThreadPool(2);
    // Sized by memory (KB), not count: a 320 px thumb is ~300 KB decoded.
    private final LruCache<String, Bitmap> thumbs =
            new LruCache<String, Bitmap>(16 * 1024) {
                @Override
                protected int sizeOf(String key, Bitmap b) {
                    return Math.max(1, b.getByteCount() / 1024);
                }
            };
    private volatile boolean destroyed;

    private int batchId = -1;

    // ---- data -------------------------------------------------------------
    /** One shot still on the gun, waiting for (or in the middle of) its
     *  upload. Newest first in {@link #pending}. */
    private static final class Pending {
        String uid;
        Bitmap thumb;
        String pinSku;
        boolean waiting; // last upload failed; retrying
    }

    private final List<Pending> pending = new ArrayList<>();
    private List<JSONObject> photos = new ArrayList<>();
    private JSONObject batch;
    private boolean readerReady = true;
    private final Map<String, String> skuTitles = new HashMap<>();
    private String lastUid;
    private String lastFiledSku;
    private String pinSku; // non-null = "Keep in SKU" mode

    /** One physical box: every photo until Next box (Steve, 2026-10-07:
     *  several photos per box). The first read that names a SKU files
     *  the box-mates too, as "auto". */
    private static final class Box {
        final List<String> uids = new ArrayList<>();
        int shots;
        String sku;
    }

    private Box box = new Box();
    private final Map<String, Box> boxOf = new HashMap<>();
    // Captured when the shutter fires, used when the JPEG arrives.
    private Box shotBox;
    private boolean shotNewBox;
    private String shotPin;
    private boolean shotPinAuto;
    private long triggerDownAt;
    private Runnable holdCue;
    private android.media.ToneGenerator tone;
    private Button stripNext;

    // ---- camera -----------------------------------------------------------
    private TextureView texture;
    private CameraDevice camera;
    private CameraCaptureSession capSession;
    private CaptureRequest.Builder previewReq;
    private ImageReader jpegReader;
    private HandlerThread camThread;
    private Handler camHandler;
    private Size previewSize;
    private int sensorOrientation = 90;
    private boolean torchOn;
    private boolean shooting;
    private MediaActionSound shutterSound;
    // What the camera did, step by step (hold the viewfinder to see it).
    private final StringBuilder camLog = new StringBuilder();
    private boolean smallSizes; // second try after a failed session
    private boolean askedPermission;
    private static final String READY_HINT =
            "Point at the SKU sticker and press the trigger";

    // ---- views ------------------------------------------------------------
    private FrameLayout root;
    private FrameLayout panelHost;
    private TextView subtitle;
    private Button segRead;
    private Button segKeep;
    private Button torchBtn;
    private TextView hint;
    private ImageView stripThumb;
    private TextView stripTitle;
    private TextView stripSub;
    private ProgressBar stripBar;
    private Button stripUndo;
    private Button stripOpen;
    private ImageView lastFolderThumb;
    private TextView lastFolderCount;
    private TextView incomingBadge;
    private View flash;
    private LinearLayout toastBar;
    private TextView toastText;
    private Button toastAction;
    private Runnable toastHide;

    private String panel = null; // null, "incoming", "folders", "folder"
    private String panelSku = null;
    private final Set<Integer> selected = new HashSet<>();
    private String folderQuery = "";
    private JSONArray folderSearch = null;

    // ===================================================================
    @Override
    protected void onCreate(Bundle saved) {
        super.onCreate(saved);
        DevLink.init(this);
        DevLink.log("cam", "Box photos onCreate, batch "
                + getIntent().getIntExtra("batch_id", -1));
        prefs = getSharedPreferences("sweep", MODE_PRIVATE);
        batchId = getIntent().getIntExtra("batch_id", -1);
        getWindow().setStatusBarColor(BG);
        getWindow().setNavigationBarColor(BG);
        if (getActionBar() != null) getActionBar().hide();
        shutterSound = new MediaActionSound();
        shutterSound.load(MediaActionSound.SHUTTER_CLICK);
        try {
            buildUi();
        } catch (Throwable t) {
            // Never a blank screen: show the fault and send it.
            DevLink.log("cam", "buildUi FAILED\n" + DevLink.trace(t));
            TextView err = new TextView(this);
            err.setText("Box photos failed to open.\n\n" + DevLink.trace(t));
            err.setTextColor(Color.WHITE);
            err.setTextSize(12);
            err.setPadding(24, 24, 24, 24);
            setContentView(err);
            return;
        }
        loadQueued();
        refresh();
        drainUploads();
    }

    @Override
    protected void onResume() {
        super.onResume();
        DevLink.resumed(this);
        if (texture == null) return; // buildUi failed; the fault is showing
        startCamThread();
        if (checkSelfPermission(Manifest.permission.CAMERA)
                != PackageManager.PERMISSION_GRANTED) {
            camStep("No camera permission yet");
            if (!askedPermission) {
                askedPermission = true;
                requestPermissions(new String[]{Manifest.permission.CAMERA},
                        REQ_CAMERA);
            } else {
                hint.setText("The camera permission is off. Allow it in "
                        + "Android Settings > Apps > TC RFID Sweep.");
            }
        } else if (texture.isAvailable()) {
            openCamera();
        } else {
            camStep("Waiting for the viewfinder surface");
        }
    }

    @Override
    protected void onPause() {
        DevLink.paused(this);
        closeCamera();
        stopCamThread();
        super.onPause();
    }

    @Override
    protected void onDestroy() {
        destroyed = true;
        if (tone != null) tone.release();
        // Let an upload in flight finish; the rest resume next time.
        uploadExec.shutdown();
        netExec.shutdownNow();
        thumbExec.shutdownNow();
        if (shutterSound != null) shutterSound.release();
        super.onDestroy();
    }

    @Override
    public void onRequestPermissionsResult(int code, String[] perms,
                                           int[] results) {
        if (code != REQ_CAMERA) return;
        if (results.length > 0
                && results[0] == PackageManager.PERMISSION_GRANTED) {
            camStep("Permission granted");
            if (texture.isAvailable()) openCamera();
        } else {
            camStep("Permission refused");
            hint.setText("The camera permission is off. Allow it in Android "
                    + "Settings > Apps > TC RFID Sweep.");
        }
    }

    @Override
    public boolean onKeyDown(int keyCode, KeyEvent event) {
        for (int k : TRIGGER_KEYS) {
            if (keyCode == k) {
                if (event.getRepeatCount() == 0 && panel == null) {
                    // A quick pull takes a photo on release; a hold
                    // (0.6 s, it beeps) is Next box.
                    triggerDownAt = System.currentTimeMillis();
                    if (holdCue != null) ui.removeCallbacks(holdCue);
                    holdCue = () -> {
                        beep(true);
                        hint.setText("Release for Next box");
                    };
                    ui.postDelayed(holdCue, 600);
                }
                return true;
            }
        }
        return super.onKeyDown(keyCode, event);
    }

    @Override
    public boolean onKeyUp(int keyCode, KeyEvent event) {
        for (int k : TRIGGER_KEYS) {
            if (keyCode == k) {
                if (holdCue != null) ui.removeCallbacks(holdCue);
                holdCue = null;
                if (panel == null && triggerDownAt > 0) {
                    long held = System.currentTimeMillis() - triggerDownAt;
                    triggerDownAt = 0;
                    if (held >= 600) {
                        hint.setText(READY_HINT);
                        nextBox();
                    } else {
                        takePhoto();
                    }
                }
                return true;
            }
        }
        return super.onKeyUp(keyCode, event);
    }

    private void beep(boolean ok) {
        try {
            if (tone == null) {
                tone = new android.media.ToneGenerator(
                        android.media.AudioManager.STREAM_NOTIFICATION, 90);
            }
            tone.startTone(ok ? android.media.ToneGenerator.TONE_PROP_ACK
                    : android.media.ToneGenerator.TONE_PROP_NACK, 150);
        } catch (Exception ignored) {
        }
    }

    /** Close the current box: its SKU's box count goes up by one. */
    private void nextBox() {
        if (box.shots == 0) {
            toast("Take a photo of this box first.", null, null);
            beep(false);
            return;
        }
        String sku = box.sku != null ? box.sku : pinSku;
        DevLink.log("cam", "Next box after " + box.shots + " photos, sku "
                + sku);
        box = new Box();
        beep(true);
        if (sku != null) {
            int n = 0;
            for (JSONObject p : folderPhotos(sku)) {
                if (p.optBoolean("new_box", true)) n++;
            }
            toast("Box done: " + sku + (n > 0 ? " (box " + n + ")" : "")
                    + ". Shoot the next one.", null, null);
        } else {
            toast("Box done. Its photos wait in Incoming until one is "
                    + "sorted.", null, null);
        }
        paintCamera();
    }

    @Override
    public void onBackPressed() {
        if ("folder".equals(panel) && !selected.isEmpty()) {
            selected.clear();
            renderPanel();
        } else if ("folder".equals(panel)) {
            openPanel("folders", null);
        } else if (panel != null) {
            closePanel();
        } else {
            super.onBackPressed();
        }
    }

    // ===================================================================
    // UI helpers
    private int dp(float v) {
        return Math.round(v * getResources().getDisplayMetrics().density);
    }

    private GradientDrawable bg(int color, float radius, int stroke) {
        GradientDrawable d = new GradientDrawable();
        d.setColor(color);
        d.setCornerRadius(dp(radius));
        if (stroke != 0) d.setStroke(dp(1), stroke);
        return d;
    }

    private TextView text(String s, float size, int color, boolean bold) {
        TextView t = new TextView(this);
        t.setText(s);
        t.setTextSize(size);
        t.setTextColor(color);
        if (bold) t.setTypeface(null, Typeface.BOLD);
        return t;
    }

    private TextView mono(String s, float size, int color) {
        TextView t = text(s, size, color, false);
        t.setTypeface(Typeface.MONOSPACE, Typeface.BOLD);
        return t;
    }

    private Button button(String label, int fill, int ink, int stroke) {
        Button b = new Button(this);
        b.setText(label);
        b.setAllCaps(false);
        b.setTextSize(14);
        b.setTypeface(null, Typeface.BOLD);
        b.setTextColor(ink);
        b.setBackground(bg(fill, 10, stroke));
        b.setMinHeight(dp(44));
        b.setMinimumHeight(dp(44));
        b.setPadding(dp(14), 0, dp(14), 0);
        b.setStateListAnimator(null);
        return b;
    }

    private Button ghost(String label) {
        return button(label, Color.TRANSPARENT, TEXT, 0xFF3A3D41);
    }

    private Button primary(String label) {
        Button b = button(label, BLUE_FILL, Color.WHITE, 0);
        b.setTextSize(15);
        b.setMinHeight(dp(52));
        b.setMinimumHeight(dp(52));
        return b;
    }

    private TextView chip(String s, int fill, int ink) {
        TextView c = text(s, 12, ink, true);
        c.setBackground(bg(fill, 999, 0));
        c.setPadding(dp(9), dp(3), dp(9), dp(3));
        return c;
    }

    private LinearLayout.LayoutParams lp(int w, int h) {
        return new LinearLayout.LayoutParams(w, h);
    }

    private LinearLayout.LayoutParams wrap() {
        return lp(ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT);
    }

    private LinearLayout.LayoutParams fillW() {
        return lp(ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT);
    }

    private LinearLayout.LayoutParams weight1() {
        return new LinearLayout.LayoutParams(0,
                ViewGroup.LayoutParams.WRAP_CONTENT, 1f);
    }

    private LinearLayout row() {
        LinearLayout r = new LinearLayout(this);
        r.setOrientation(LinearLayout.HORIZONTAL);
        r.setGravity(Gravity.CENTER_VERTICAL);
        return r;
    }

    private LinearLayout col() {
        LinearLayout c = new LinearLayout(this);
        c.setOrientation(LinearLayout.VERTICAL);
        return c;
    }

    private static String plural(int n, String one, String many) {
        return n + " " + (n == 1 ? one : many);
    }

    private static String norm(String s) {
        return s == null ? "" : s.toUpperCase(Locale.US)
                .replaceAll("[^A-Z0-9]", "");
    }

    // ===================================================================
    // Camera screen
    private void buildUi() {
        root = new FrameLayout(this);
        root.setBackgroundColor(BG);

        LinearLayout cam = col();
        cam.setBackgroundColor(BG);

        // Header
        LinearLayout head = row();
        head.setPadding(dp(16), dp(10), dp(12), dp(8));
        LinearLayout ht = col();
        LinearLayout titleRow = row();
        titleRow.addView(text("Box photos", 19, TEXT, true));
        TextView dev = chip("DEV", WARN_BG, WARN);
        dev.setTextSize(11);
        LinearLayout.LayoutParams dl = wrap();
        dl.leftMargin = dp(8);
        titleRow.addView(dev, dl);
        ht.addView(titleRow);
        subtitle = text("", 12, MUTED, false);
        ht.addView(subtitle);
        head.addView(ht, weight1());
        Button folders = button("Folders", CHIP, TEXT, 0);
        folders.setOnClickListener(v -> openPanel("folders", null));
        head.addView(folders);
        cam.addView(head, fillW());

        // Read the box / Keep in SKU
        LinearLayout seg = row();
        seg.setPadding(dp(12), 0, dp(12), dp(10));
        segRead = button("Read the box", SOFT, TEXT, BLUE);
        segKeep = button("Keep in a folder", Color.TRANSPARENT, MUTED, LINE);
        segRead.setOnClickListener(v -> {
            pinSku = null;
            paintCamera();
        });
        segKeep.setOnClickListener(v -> {
            String sku = pinSku != null ? pinSku : lastFiledSku;
            if (sku == null) {
                toast("Pick a folder first: open Folders, or take a photo "
                        + "the reader files.", null, null);
                return;
            }
            pinSku = sku;
            paintCamera();
        });
        LinearLayout.LayoutParams s1 = weight1();
        s1.rightMargin = dp(8);
        seg.addView(segRead, s1);
        seg.addView(segKeep, weight1());
        cam.addView(seg, fillW());

        // Viewfinder
        FrameLayout finder = new FrameLayout(this);
        finder.setBackground(bg(0xFF0C0D0E, 14, 0));
        finder.setClipToOutline(true);
        texture = new TextureView(this);
        texture.setSurfaceTextureListener(new TextureView.SurfaceTextureListener() {
            @Override
            public void onSurfaceTextureAvailable(SurfaceTexture st, int w, int h) {
                camStep("Viewfinder surface ready " + w + "x" + h);
                if (checkSelfPermission(Manifest.permission.CAMERA)
                        == PackageManager.PERMISSION_GRANTED) {
                    openCamera();
                }
            }

            @Override
            public void onSurfaceTextureSizeChanged(SurfaceTexture st, int w, int h) {
                fitPreview();
            }

            @Override
            public boolean onSurfaceTextureDestroyed(SurfaceTexture st) {
                return true;
            }

            @Override
            public void onSurfaceTextureUpdated(SurfaceTexture st) {
            }
        });
        finder.addView(texture, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT));
        finder.addView(new CornersView(this), new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT));
        flash = new View(this);
        flash.setBackgroundColor(0x55FFFFFF);
        flash.setVisibility(View.GONE);
        finder.addView(flash, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT));
        TextView range = text("Hold 15 to 35 cm from the sticker", 12,
                Color.WHITE, true);
        range.setBackground(bg(0x99000000, 999, 0));
        range.setPadding(dp(10), dp(6), dp(10), dp(6));
        FrameLayout.LayoutParams rl = new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
                Gravity.TOP | Gravity.START);
        rl.setMargins(dp(12), dp(12), 0, 0);
        finder.addView(range, rl);
        torchBtn = button("Torch", 0x99000000, Color.WHITE, 0);
        torchBtn.setTextSize(12);
        torchBtn.setOnClickListener(v -> setTorch(!torchOn));
        FrameLayout.LayoutParams tl = new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, dp(40),
                Gravity.TOP | Gravity.END);
        tl.setMargins(0, dp(8), dp(10), 0);
        finder.addView(torchBtn, tl);
        hint = text("Point at the SKU sticker and press the trigger", 13,
                Color.WHITE, true);
        hint.setGravity(Gravity.CENTER);
        hint.setBackground(bg(0xA8000000, 999, 0));
        hint.setPadding(dp(14), dp(8), dp(14), dp(8));
        FrameLayout.LayoutParams hl = new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT,
                Gravity.BOTTOM | Gravity.CENTER_HORIZONTAL);
        hl.setMargins(dp(12), 0, dp(12), dp(16));
        finder.addView(hint, hl);
        finder.setOnClickListener(v -> takePhoto());
        finder.setOnLongClickListener(v -> {
            showCamLog();
            return true;
        });
        LinearLayout.LayoutParams fl = new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f);
        fl.leftMargin = dp(12);
        fl.rightMargin = dp(12);
        cam.addView(finder, fl);

        // Result strip
        LinearLayout strip = row();
        strip.setBackground(bg(CARD, 12, 0));
        strip.setPadding(dp(12), dp(10), dp(12), dp(10));
        strip.setMinimumHeight(dp(72));
        stripThumb = new ImageView(this);
        stripThumb.setScaleType(ImageView.ScaleType.CENTER_CROP);
        stripThumb.setBackground(bg(CHIP, 8, 0));
        stripThumb.setClipToOutline(true);
        strip.addView(stripThumb, lp(dp(48), dp(48)));
        LinearLayout st = col();
        st.setPadding(dp(12), 0, dp(8), 0);
        stripTitle = text("", 14, TEXT, true);
        stripTitle.setSingleLine(true);
        stripSub = text("", 12, MUTED, false);
        stripSub.setSingleLine(true);
        stripSub.setEllipsize(android.text.TextUtils.TruncateAt.END);
        stripBar = new ProgressBar(this, null,
                android.R.attr.progressBarStyleHorizontal);
        stripBar.setIndeterminate(true);
        stripBar.setVisibility(View.GONE);
        st.addView(stripTitle);
        st.addView(stripSub);
        st.addView(stripBar, fillW());
        strip.addView(st, weight1());
        stripOpen = ghost("Sort");
        stripOpen.setOnClickListener(v -> openPanel("incoming", null));
        strip.addView(stripOpen);
        stripUndo = ghost("Undo");
        stripUndo.setOnClickListener(v -> undoLast());
        LinearLayout.LayoutParams ul = wrap();
        ul.leftMargin = dp(6);
        strip.addView(stripUndo, ul);
        stripNext = button("Next box", BLUE_FILL, Color.WHITE, 0);
        stripNext.setOnClickListener(v -> nextBox());
        LinearLayout.LayoutParams nl = wrap();
        nl.leftMargin = dp(6);
        strip.addView(stripNext, nl);
        LinearLayout.LayoutParams stl = fillW();
        stl.setMargins(dp(12), dp(10), dp(12), 0);
        cam.addView(strip, stl);

        // Bottom: last folder, shutter, Incoming
        LinearLayout bottom = row();
        bottom.setPadding(dp(24), dp(12), dp(24), dp(16));
        FrameLayout lastBox = new FrameLayout(this);
        lastBox.setBackground(bg(CHIP, 10, 0));
        lastBox.setClipToOutline(true);
        lastFolderThumb = new ImageView(this);
        lastFolderThumb.setScaleType(ImageView.ScaleType.CENTER_CROP);
        lastBox.addView(lastFolderThumb, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT));
        lastFolderCount = text("", 11, TEXT, true);
        lastFolderCount.setBackground(bg(BG, 999, 0));
        lastFolderCount.setPadding(dp(6), dp(1), dp(6), dp(1));
        FrameLayout.LayoutParams cl = new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT,
                ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.TOP | Gravity.END);
        cl.setMargins(0, dp(3), dp(3), 0);
        lastBox.addView(lastFolderCount, cl);
        lastBox.setOnClickListener(v -> {
            String sku = pinSku != null ? pinSku : lastFiledSku;
            if (sku != null) openPanel("folder", sku);
            else openPanel("folders", null);
        });
        bottom.addView(lastBox, lp(dp(56), dp(56)));
        View spacerA = new View(this);
        // 1 px tall: a bare View asked to WRAP_CONTENT fills the whole
        // height, which squeezed the viewfinder to nothing (4.38).
        bottom.addView(spacerA, new LinearLayout.LayoutParams(0, 1, 1f));
        LinearLayout shutterCol = col();
        shutterCol.setGravity(Gravity.CENTER_HORIZONTAL);
        ShutterView shutter = new ShutterView(this);
        shutter.setContentDescription("Take photo");
        shutter.setOnClickListener(v -> takePhoto());
        shutterCol.addView(shutter, lp(dp(78), dp(78)));
        TextView sh = text("Trigger or tap", 12, MUTED, false);
        sh.setGravity(Gravity.CENTER);
        LinearLayout.LayoutParams shl = wrap();
        shl.topMargin = dp(4);
        shutterCol.addView(sh, shl);
        bottom.addView(shutterCol);
        View spacerB = new View(this);
        bottom.addView(spacerB, new LinearLayout.LayoutParams(0, 1, 1f));
        FrameLayout inBox = new FrameLayout(this);
        Button inBtn = button("In", CHIP, TEXT, 0);
        inBtn.setText("Incoming");
        inBtn.setTextSize(11);
        inBtn.setBackground(bg(CHIP, 28, 0));
        inBtn.setPadding(0, 0, 0, 0);
        inBtn.setOnClickListener(v -> openPanel("incoming", null));
        inBox.addView(inBtn, new FrameLayout.LayoutParams(dp(64), dp(56),
                Gravity.CENTER));
        incomingBadge = text("0", 12, BG, true);
        incomingBadge.setGravity(Gravity.CENTER);
        incomingBadge.setBackground(bg(WARN, 999, 0));
        incomingBadge.setPadding(dp(6), 0, dp(6), 0);
        incomingBadge.setMinWidth(dp(22));
        FrameLayout.LayoutParams bl = new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.WRAP_CONTENT, dp(22),
                Gravity.TOP | Gravity.END);
        inBox.addView(incomingBadge, bl);
        bottom.addView(inBox, lp(dp(70), dp(62)));
        cam.addView(bottom, fillW());

        root.addView(cam, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT));

        panelHost = new FrameLayout(this);
        panelHost.setBackgroundColor(BG);
        panelHost.setClickable(true);
        panelHost.setVisibility(View.GONE);
        root.addView(panelHost, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT));

        toastBar = row();
        toastBar.setBackground(bg(0xFF2B2E31, 10, 0));
        toastBar.setPadding(dp(14), dp(6), dp(8), dp(6));
        toastBar.setElevation(dp(8));
        toastText = text("", 14, TEXT, false);
        toastBar.addView(toastText, weight1());
        toastAction = button("", Color.TRANSPARENT, BLUE_TEXT, 0);
        toastBar.addView(toastAction);
        toastBar.setVisibility(View.GONE);
        FrameLayout.LayoutParams tbl = new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.WRAP_CONTENT, Gravity.BOTTOM);
        tbl.setMargins(dp(12), 0, dp(12), dp(110));
        root.addView(toastBar, tbl);

        setContentView(root);
        paintCamera();
    }

    /** The four aiming corners over the viewfinder. */
    private final class CornersView extends View {
        private final Paint p = new Paint(Paint.ANTI_ALIAS_FLAG);

        CornersView(Context c) {
            super(c);
            p.setColor(0xD9FFFFFF);
            p.setStyle(Paint.Style.STROKE);
            p.setStrokeWidth(dp(3));
            p.setStrokeCap(Paint.Cap.ROUND);
        }

        @Override
        protected void onDraw(Canvas c) {
            float m = dp(22), l = dp(30), w = getWidth(), h = getHeight();
            c.drawLine(m, m, m + l, m, p);
            c.drawLine(m, m, m, m + l, p);
            c.drawLine(w - m, m, w - m - l, m, p);
            c.drawLine(w - m, m, w - m, m + l, p);
            c.drawLine(m, h - m, m + l, h - m, p);
            c.drawLine(m, h - m, m, h - m - l, p);
            c.drawLine(w - m, h - m, w - m - l, h - m, p);
            c.drawLine(w - m, h - m, w - m, h - m - l, p);
        }
    }

    /** The round shutter: a ring with a filled centre. */
    private final class ShutterView extends View {
        private final Paint ring = new Paint(Paint.ANTI_ALIAS_FLAG);
        private final Paint fill = new Paint(Paint.ANTI_ALIAS_FLAG);

        ShutterView(Context c) {
            super(c);
            ring.setColor(TEXT);
            ring.setStyle(Paint.Style.STROKE);
            ring.setStrokeWidth(dp(4));
            setClickable(true);
            setFocusable(true);
        }

        @Override
        protected void onDraw(Canvas c) {
            float cx = getWidth() / 2f, cy = getHeight() / 2f;
            float r = Math.min(cx, cy) - dp(2);
            c.drawCircle(cx, cy, r, ring);
            fill.setColor(shooting || isPressed() ? MUTED : Color.WHITE);
            c.drawCircle(cx, cy, r - dp(8), fill);
        }

        @Override
        protected void drawableStateChanged() {
            super.drawableStateChanged();
            invalidate();
        }
    }

    private void paintCamera() {
        if (segRead == null) return;
        boolean keep = pinSku != null;
        segRead.setBackground(bg(keep ? Color.TRANSPARENT : SOFT, 10,
                keep ? LINE : BLUE));
        segRead.setTextColor(keep ? MUTED : TEXT);
        String keepSku = pinSku != null ? pinSku : lastFiledSku;
        segKeep.setText(keepSku == null ? "Keep in a folder"
                : "Keep in " + keepSku);
        segKeep.setBackground(bg(keep ? SOFT : Color.TRANSPARENT, 10,
                keep ? BLUE : LINE));
        segKeep.setTextColor(keep ? TEXT : MUTED);

        String batchLine;
        if (batch != null) {
            int n = batch.optJSONArray("products") == null ? 0
                    : batch.optJSONArray("products").length();
            batchLine = "Batch: " + batch.optString("label")
                    + (n > 0 ? " · checks its " + plural(n, "product",
                    "products") + " first" : "");
        } else {
            batchLine = "No batch open · checks the whole catalog";
        }
        if (!readerReady) batchLine = "The reader is off · photos sort by hand";
        subtitle.setText(batchLine);

        torchBtn.setText(torchOn ? "Torch on" : "Torch");
        torchBtn.setBackground(bg(torchOn ? WARN : 0x99000000, 10, 0));
        torchBtn.setTextColor(torchOn ? BG : Color.WHITE);

        int needs = incoming().size();
        incomingBadge.setText(String.valueOf(needs + pending.size()));
        incomingBadge.setVisibility(needs + pending.size() > 0
                ? View.VISIBLE : View.GONE);

        String folderSku = pinSku != null ? pinSku : lastFiledSku;
        List<JSONObject> inFolder = folderSku == null
                ? new ArrayList<>() : folderPhotos(folderSku);
        lastFolderCount.setText(String.valueOf(inFolder.size()));
        lastFolderCount.setVisibility(inFolder.isEmpty() ? View.GONE
                : View.VISIBLE);
        if (!inFolder.isEmpty()) {
            loadThumb(lastFolderThumb, inFolder.get(0));
        } else {
            lastFolderThumb.setImageDrawable(null);
        }
        paintStrip();
    }

    private void paintStrip() {
        paintStripPhoto();
        // A box in progress: say so, and offer Next box instead of Sort.
        boolean inBox = box.shots > 0;
        stripNext.setVisibility(inBox ? View.VISIBLE : View.GONE);
        if (inBox) {
            stripOpen.setVisibility(View.GONE);
            String sku = box.sku != null ? box.sku : pinSku;
            stripSub.setVisibility(View.VISIBLE);
            stripSub.setTextColor(MUTED);
            stripSub.setText("This box: " + plural(box.shots, "photo",
                    "photos") + (sku != null ? " · " + sku : " · not read yet")
                    + " · hold the trigger for Next box");
        }
    }

    private void paintStripPhoto() {
        stripBar.setVisibility(View.GONE);
        stripUndo.setVisibility(View.GONE);
        stripOpen.setVisibility(View.GONE);
        stripSub.setTextColor(MUTED);
        Pending pend = lastUid == null ? null : pendingByUid(lastUid);
        JSONObject ph = lastUid == null ? null : photoByUid(lastUid);
        if (pend != null) {
            stripThumb.setImageBitmap(pend.thumb);
            stripTitle.setText(pend.pinSku != null
                    ? "Saving to " + pend.pinSku : "Reading the box");
            stripSub.setText(pend.waiting
                    ? "Waiting for the server · it retries by itself" : "");
            stripBar.setVisibility(pend.waiting ? View.GONE : View.VISIBLE);
            stripSub.setVisibility(pend.waiting ? View.VISIBLE : View.GONE);
            stripUndo.setVisibility(View.VISIBLE);
            return;
        }
        stripSub.setVisibility(View.VISIBLE);
        if (ph == null) {
            stripThumb.setImageDrawable(null);
            stripTitle.setText(photos.isEmpty() ? "No photos yet"
                    : plural(photos.size(), "photo", "photos")
                    + " in this shipment");
            stripSub.setText("Shoot the side with the SKU sticker");
            return;
        }
        loadThumb(stripThumb, ph);
        String status = ph.optString("status");
        String sku = ph.optString("sku", "");
        stripUndo.setVisibility(View.VISIBLE);
        if ("auto".equals(status) || "confirmed".equals(status)) {
            stripTitle.setText("Filed to " + sku);
            stripSub.setText(("confirmed".equals(status)
                    ? "Kept in this folder" : "Auto-sorted, check later")
                    + titleSuffix(sku));
        } else if ("ask".equals(status)) {
            JSONArray g = ph.optJSONArray("guesses");
            stripTitle.setText("Needs a tap");
            stripSub.setText((g == null ? 0 : g.length()) > 1
                    ? "It could be " + guessList(g) : "Not sure: check it");
            stripSub.setTextColor(WARN);
            stripOpen.setVisibility(View.VISIBLE);
        } else {
            stripTitle.setText("No SKU found");
            String err = ph.optString("ocr_error", "");
            stripSub.setText(!err.isEmpty() && !"null".equals(err) ? err
                    : "Sort it in Incoming, or shoot the sticker side");
            stripSub.setTextColor(WARN);
            stripOpen.setVisibility(View.VISIBLE);
        }
    }

    private String titleSuffix(String sku) {
        String t = titleFor(sku);
        return t == null ? "" : " · " + t;
    }

    private static String guessList(JSONArray g) {
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < g.length(); i++) {
            if (i > 0) sb.append(i == g.length() - 1 ? " or " : ", ");
            sb.append(g.optJSONObject(i).optString("sku"));
        }
        return sb.toString();
    }

    // ===================================================================
    // Data
    private List<JSONObject> incoming() {
        List<JSONObject> out = new ArrayList<>();
        for (JSONObject p : photos) {
            String s = p.optString("status");
            if ("ask".equals(s) || "none".equals(s)) out.add(p);
        }
        return out;
    }

    private List<JSONObject> folderPhotos(String sku) {
        List<JSONObject> out = new ArrayList<>();
        for (JSONObject p : photos) {
            String s = p.optString("status");
            if (("auto".equals(s) || "confirmed".equals(s))
                    && sku.equalsIgnoreCase(p.optString("sku"))) {
                out.add(p);
            }
        }
        return out;
    }

    /** SKU -> its photos, newest folder activity first. */
    private LinkedHashMap<String, List<JSONObject>> folders() {
        LinkedHashMap<String, List<JSONObject>> out = new LinkedHashMap<>();
        for (JSONObject p : photos) {
            String s = p.optString("status");
            if (!"auto".equals(s) && !"confirmed".equals(s)) continue;
            String sku = p.optString("sku");
            List<JSONObject> l = out.get(sku);
            if (l == null) {
                l = new ArrayList<>();
                out.put(sku, l);
            }
            l.add(p);
        }
        return out;
    }

    private String titleFor(String sku) {
        if (sku == null) return null;
        String t = skuTitles.get(sku.toUpperCase(Locale.US));
        return t == null || t.isEmpty() || "null".equals(t) ? null : t;
    }

    private JSONObject photoByUid(String uid) {
        for (JSONObject p : photos) {
            if (uid.equals(p.optString("uid"))) return p;
        }
        return null;
    }

    private JSONObject photoById(int id) {
        for (JSONObject p : photos) if (p.optInt("id") == id) return p;
        return null;
    }

    private Pending pendingByUid(String uid) {
        for (Pending p : pending) if (uid.equals(p.uid)) return p;
        return null;
    }

    private void absorbTitles(JSONObject p) {
        String sku = p.optString("sku", "");
        String title = p.optString("title", "");
        if (!sku.isEmpty() && !"null".equals(sku) && !title.isEmpty()
                && !"null".equals(title)) {
            skuTitles.put(sku.toUpperCase(Locale.US), title);
        }
        JSONArray g = p.optJSONArray("guesses");
        if (g != null) {
            for (int i = 0; i < g.length(); i++) {
                JSONObject o = g.optJSONObject(i);
                if (o != null && !o.optString("title").isEmpty()) {
                    skuTitles.put(o.optString("sku").toUpperCase(Locale.US),
                            o.optString("title"));
                }
            }
        }
    }

    /** Reload the shipment from the server, then repaint. */
    private void refresh() {
        netExec.execute(() -> {
            try {
                JSONObject r = api("GET", "/api/boxphotos/current"
                        + (batchId >= 0 ? "?batch_id=" + batchId : ""), null);
                JSONArray arr = r.optJSONArray("photos");
                List<JSONObject> list = new ArrayList<>();
                if (arr != null) {
                    for (int i = 0; i < arr.length(); i++) {
                        list.add(arr.getJSONObject(i));
                    }
                }
                JSONObject b = r.optJSONObject("batch");
                boolean ready = r.optBoolean("reader_ready", true);
                ui.post(() -> {
                    photos = list;
                    batch = b;
                    readerReady = ready;
                    for (JSONObject p : photos) absorbTitles(p);
                    if (batch != null) {
                        JSONArray pr = batch.optJSONArray("products");
                        for (int i = 0; pr != null && i < pr.length(); i++) {
                            JSONObject o = pr.optJSONObject(i);
                            skuTitles.put(o.optString("sku")
                                    .toUpperCase(Locale.US), o.optString("title"));
                        }
                    }
                    paintCamera();
                    renderPanel();
                });
            } catch (Exception e) {
                ui.post(() -> toast("Couldn't load the photos: "
                        + e.getMessage(), "Retry", this::refresh));
            }
        });
    }

    // ===================================================================
    // Camera2
    private void startCamThread() {
        camThread = new HandlerThread("boxcam");
        camThread.start();
        camHandler = new Handler(camThread.getLooper());
    }

    private void stopCamThread() {
        if (camThread == null) return;
        camThread.quitSafely();
        try {
            camThread.join(1500);
        } catch (InterruptedException ignored) {
        }
        camThread = null;
        camHandler = null;
    }

    private static Size pick(Size[] sizes, int maxLong) {
        Size best = null;
        for (Size s : sizes) {
            boolean fourThree = s.getWidth() * 3 == s.getHeight() * 4;
            int longEdge = Math.max(s.getWidth(), s.getHeight());
            if (!fourThree || longEdge > maxLong) continue;
            if (best == null || (long) s.getWidth() * s.getHeight()
                    > (long) best.getWidth() * best.getHeight()) {
                best = s;
            }
        }
        if (best == null) {
            for (Size s : sizes) {
                if (Math.max(s.getWidth(), s.getHeight()) > maxLong) continue;
                if (best == null || (long) s.getWidth() * s.getHeight()
                        > (long) best.getWidth() * best.getHeight()) {
                    best = s;
                }
            }
        }
        return best != null ? best : sizes[0];
    }

    /** Record a camera step (main thread or camera thread). */
    private void camStep(String msg) {
        String line = new SimpleDateFormat("HH:mm:ss", Locale.US)
                .format(new Date()) + "  " + msg;
        DevLink.log("cam", msg);
        synchronized (camLog) {
            camLog.append(line).append('\n');
            if (camLog.length() > 6000) {
                camLog.delete(0, camLog.length() - 6000);
            }
        }
    }

    /** A failure the operator must see: on the viewfinder and in the log. */
    private void camFail(String msg) {
        camStep("FAILED: " + msg);
        ui.post(() -> hint.setText(msg
                + " Tap to try again; hold for details."));
    }

    /** For the debug link: restart the camera from scratch. */
    void restartCamera() {
        camStep("Restart requested remotely");
        closeCamera();
        if (camHandler == null) startCamThread();
        openCamera();
    }

    private void showCamLog() {
        TextView t = text(camLogText(), 12, TEXT, false);
        t.setTypeface(Typeface.MONOSPACE);
        t.setTextIsSelectable(true);
        t.setPadding(dp(16), dp(8), dp(16), dp(8));
        ScrollView sv = new ScrollView(this);
        sv.addView(t);
        new AlertDialog.Builder(this, android.R.style.Theme_Material_Dialog_Alert)
                .setTitle("Camera details")
                .setView(sv)
                .setPositiveButton("Close", null)
                .setNeutralButton("Restart camera", (d, w) -> restartCamera())
                .show();
    }

    /** Permission, surface, cameras and the step log, as text. */
    String camLogText() {
        StringBuilder sb = new StringBuilder();
        sb.append("Permission: ").append(checkSelfPermission(
                Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED
                ? "granted" : "NOT granted").append('\n');
        sb.append("Viewfinder surface: ").append(texture.isAvailable()
                ? texture.getWidth() + "x" + texture.getHeight() : "not ready")
                .append('\n');
        sb.append("Camera open: ").append(camera != null)
                .append(", session: ").append(capSession != null)
                .append("\n\n");
        try {
            CameraManager mgr = (CameraManager) getSystemService(CAMERA_SERVICE);
            for (String c : mgr.getCameraIdList()) {
                CameraCharacteristics ch = mgr.getCameraCharacteristics(c);
                Integer f = ch.get(CameraCharacteristics.LENS_FACING);
                Integer lvl = ch.get(
                        CameraCharacteristics.INFO_SUPPORTED_HARDWARE_LEVEL);
                sb.append("Camera ").append(c).append(": ")
                        .append(f == null ? "?" : f == 0 ? "front"
                                : f == 1 ? "back" : "external")
                        .append(", level ").append(lvl).append('\n');
            }
        } catch (Exception e) {
            sb.append("Camera list failed: ").append(e).append('\n');
        }
        synchronized (camLog) {
            sb.append('\n').append(camLog);
        }
        return sb.toString();
    }

    private void openCamera() {
        if (camera != null) return;
        if (camHandler == null) {
            camStep("openCamera: no camera thread yet (screen paused)");
            return;
        }
        hint.setText("Starting the camera…");
        camStep("Opening the camera");
        CameraManager mgr = (CameraManager) getSystemService(CAMERA_SERVICE);
        try {
            String id = null;
            CameraCharacteristics chars = null;
            String[] ids = mgr.getCameraIdList();
            camStep("Cameras reported: " + ids.length);
            // The back camera; any camera at all when none says "back".
            for (String c : ids) {
                CameraCharacteristics ch = mgr.getCameraCharacteristics(c);
                Integer facing = ch.get(CameraCharacteristics.LENS_FACING);
                if (facing != null
                        && facing == CameraCharacteristics.LENS_FACING_BACK) {
                    id = c;
                    chars = ch;
                    break;
                }
            }
            if (id == null && ids.length > 0) {
                id = ids[0];
                chars = mgr.getCameraCharacteristics(id);
                camStep("No back camera; using camera " + id);
            }
            if (id == null) {
                camFail("Android reports no camera on this device.");
                return;
            }
            StreamConfigurationMap map = chars.get(
                    CameraCharacteristics.SCALER_STREAM_CONFIGURATION_MAP);
            Integer so = chars.get(CameraCharacteristics.SENSOR_ORIENTATION);
            sensorOrientation = so == null ? 90 : so;
            previewSize = pick(map.getOutputSizes(SurfaceTexture.class),
                    smallSizes ? 1280 : 1600);
            Size jpeg = pick(map.getOutputSizes(ImageFormat.JPEG),
                    smallSizes ? 1920 : 3300);
            camStep("Camera " + id + ": preview " + previewSize + ", photo "
                    + jpeg + ", sensor " + sensorOrientation + " deg");
            jpegReader = ImageReader.newInstance(jpeg.getWidth(),
                    jpeg.getHeight(), ImageFormat.JPEG, 2);
            jpegReader.setOnImageAvailableListener(this::onJpeg, camHandler);
            mgr.openCamera(id, new CameraDevice.StateCallback() {
                @Override
                public void onOpened(CameraDevice d) {
                    camStep("Camera opened");
                    camera = d;
                    startPreview();
                }

                @Override
                public void onDisconnected(CameraDevice d) {
                    d.close();
                    camera = null;
                    capSession = null;
                    camFail("Another app took the camera (the scanner's QR "
                            + "mode, maybe).");
                }

                @Override
                public void onError(CameraDevice d, int error) {
                    d.close();
                    camera = null;
                    capSession = null;
                    String why = error == ERROR_CAMERA_IN_USE
                            ? "another app is using the camera"
                            : error == ERROR_MAX_CAMERAS_IN_USE
                            ? "too many cameras are open"
                            : error == ERROR_CAMERA_DISABLED
                            ? "the camera is disabled by a device policy"
                            : error == ERROR_CAMERA_DEVICE
                            ? "the camera hardware reported a fault"
                            : "the camera service failed";
                    camFail("The camera wouldn't open: " + why + " (error "
                            + error + ").");
                }
            }, camHandler);
        } catch (SecurityException e) {
            camFail("Android refused the camera: " + e.getMessage());
        } catch (Exception e) {
            camFail("The camera isn't available: " + e);
        }
    }

    private void startPreview() {
        try {
            SurfaceTexture st = texture.getSurfaceTexture();
            if (st == null || camera == null) {
                camFail("The viewfinder wasn't ready when the camera opened.");
                return;
            }
            st.setDefaultBufferSize(previewSize.getWidth(),
                    previewSize.getHeight());
            Surface surface = new Surface(st);
            previewReq = camera.createCaptureRequest(
                    CameraDevice.TEMPLATE_PREVIEW);
            previewReq.addTarget(surface);
            camera.createCaptureSession(
                    Arrays.asList(surface, jpegReader.getSurface()),
                    new CameraCaptureSession.StateCallback() {
                        @Override
                        public void onConfigured(CameraCaptureSession s) {
                            if (camera == null) return;
                            capSession = s;
                            camStep("Preview session ready");
                            applyPreview();
                            ui.post(() -> {
                                fitPreview();
                                hint.setText(READY_HINT);
                            });
                        }

                        @Override
                        public void onConfigureFailed(CameraCaptureSession s) {
                            if (!smallSizes) {
                                // Some camera drivers refuse the big
                                // photo size alongside the preview.
                                camStep("Session refused; retrying smaller");
                                smallSizes = true;
                                ui.post(() -> {
                                    closeCamera();
                                    openCamera();
                                });
                            } else {
                                camFail("The camera refused the preview "
                                        + "set-up.");
                            }
                        }
                    }, camHandler);
        } catch (Exception e) {
            camFail("The preview couldn't start: " + e);
        }
    }

    /** Continuous autofocus aimed at the middle (a hard focus lock only
     *  keeps a couple of centimetres sharp at box distance), torch as
     *  set. */
    private void applyPreview() {
        if (capSession == null || previewReq == null) return;
        try {
            previewReq.set(CaptureRequest.CONTROL_MODE,
                    CameraMetadata.CONTROL_MODE_AUTO);
            previewReq.set(CaptureRequest.CONTROL_AF_MODE,
                    CaptureRequest.CONTROL_AF_MODE_CONTINUOUS_PICTURE);
            previewReq.set(CaptureRequest.CONTROL_AE_MODE,
                    CaptureRequest.CONTROL_AE_MODE_ON);
            previewReq.set(CaptureRequest.FLASH_MODE, torchOn
                    ? CaptureRequest.FLASH_MODE_TORCH
                    : CaptureRequest.FLASH_MODE_OFF);
            capSession.setRepeatingRequest(previewReq.build(), null,
                    camHandler);
        } catch (Exception e) {
            camFail("The camera stopped: " + e.getMessage());
        }
    }

    private void setTorch(boolean on) {
        torchOn = on;
        paintCamera();
        if (camHandler != null) camHandler.post(this::applyPreview);
    }

    /** Centre-crop the preview into the viewfinder (portrait). */
    private void fitPreview() {
        if (previewSize == null) return;
        float vw = texture.getWidth(), vh = texture.getHeight();
        if (vw == 0 || vh == 0) return;
        float cw = previewSize.getHeight(), ch = previewSize.getWidth();
        float scale = Math.max(vw / cw, vh / ch);
        Matrix m = new Matrix();
        m.setScale(cw * scale / vw, ch * scale / vh, vw / 2f, vh / 2f);
        texture.setTransform(m);
    }

    private void closeCamera() {
        try {
            if (capSession != null) capSession.close();
        } catch (Exception ignored) {
        }
        capSession = null;
        if (camera != null) camera.close();
        camera = null;
        if (jpegReader != null) jpegReader.close();
        jpegReader = null;
        shooting = false;
    }

    private void takePhoto() {
        if (shooting) return;
        if (camera == null || capSession == null || jpegReader == null) {
            toast("The camera isn't ready. Trying to start it again; hold "
                    + "the viewfinder for details.", null, null);
            closeCamera();
            if (camHandler == null) startCamThread();
            openCamera();
            return;
        }
        shooting = true;
        shotBox = box;
        shotNewBox = box.shots == 0;
        box.shots++;
        shotPin = pinSku != null ? pinSku : box.sku;
        shotPinAuto = pinSku == null && box.sku != null;
        // Never stay stuck if a capture silently produces nothing.
        ui.postDelayed(() -> shooting = false, 5000);
        try {
            CaptureRequest.Builder still = camera.createCaptureRequest(
                    CameraDevice.TEMPLATE_STILL_CAPTURE);
            still.addTarget(jpegReader.getSurface());
            still.set(CaptureRequest.CONTROL_AF_MODE,
                    CaptureRequest.CONTROL_AF_MODE_CONTINUOUS_PICTURE);
            still.set(CaptureRequest.CONTROL_AE_MODE,
                    CaptureRequest.CONTROL_AE_MODE_ON);
            still.set(CaptureRequest.FLASH_MODE, torchOn
                    ? CaptureRequest.FLASH_MODE_TORCH
                    : CaptureRequest.FLASH_MODE_OFF);
            still.set(CaptureRequest.JPEG_ORIENTATION, sensorOrientation);
            still.set(CaptureRequest.JPEG_QUALITY, (byte) 90);
            capSession.capture(still.build(), null, camHandler);
            shutterSound.play(MediaActionSound.SHUTTER_CLICK);
            flash.setVisibility(View.VISIBLE);
            ui.postDelayed(() -> flash.setVisibility(View.GONE), 120);
        } catch (Exception e) {
            shooting = false;
            if (shotBox != null && shotBox.shots > 0) shotBox.shots--;
            toast("The photo didn't take: " + e.getMessage(), null, null);
        }
    }

    /** Camera thread: the JPEG arrived. Straighten, shrink, queue. */
    private void onJpeg(ImageReader reader) {
        byte[] raw;
        try (Image img = reader.acquireNextImage()) {
            if (img == null) return;
            ByteBuffer buf = img.getPlanes()[0].getBuffer();
            raw = new byte[buf.remaining()];
            buf.get(raw);
        } catch (Exception e) {
            ui.post(() -> {
                shooting = false;
                toast("The photo didn't save: " + e.getMessage(), null, null);
            });
            return;
        }
        final String pin = shotPin;
        final boolean pinAuto = shotPinAuto;
        final boolean newBox = shotNewBox;
        final Box inBox = shotBox;
        final String uid = new SimpleDateFormat("yyyyMMdd-HHmmss", Locale.US)
                .format(new Date()) + "-" + Integer.toHexString(
                0x100000 + new Random().nextInt(0xEFFFFF));
        try {
            byte[][] out = shrink(raw);
            File dir = queueDir();
            write(new File(dir, uid + ".jpg"), out[0]);
            write(new File(dir, uid + "_t.jpg"), out[1]);
            JSONObject meta = new JSONObject()
                    .put("uid", uid)
                    .put("batch_id", batchId)
                    .put("new_box", newBox)
                    .put("pin_sku", pin == null ? JSONObject.NULL : pin)
                    .put("pin_auto", pinAuto)
                    .put("worker", prefs.getString("worker_name", ""));
            write(new File(dir, uid + ".json"),
                    meta.toString().getBytes(StandardCharsets.UTF_8));
            Bitmap thumb = BitmapFactory.decodeByteArray(out[1], 0,
                    out[1].length);
            ui.post(() -> {
                Pending p = new Pending();
                p.uid = uid;
                p.thumb = thumb;
                p.pinSku = pin;
                pending.add(0, p);
                if (inBox != null) {
                    inBox.uids.add(uid);
                    boxOf.put(uid, inBox);
                }
                thumbs.put("u:" + uid, thumb);
                lastUid = uid;
                shooting = false;
                paintCamera();
                renderPanel();
                drainUploads();
            });
        } catch (Exception e) {
            ui.post(() -> {
                shooting = false;
                if (inBox != null && inBox.shots > 0) inBox.shots--;
                toast("The photo didn't save: " + e.getMessage(), null, null);
            });
        }
    }

    /** Upright JPEG at most 1600 px on its long edge, plus a 320 px
     *  thumbnail. */
    private static byte[][] shrink(byte[] raw) throws Exception {
        int degrees = 0;
        try (InputStream in = new ByteArrayInputStream(raw)) {
            ExifInterface ex = new ExifInterface(in);
            int o = ex.getAttributeInt(ExifInterface.TAG_ORIENTATION,
                    ExifInterface.ORIENTATION_NORMAL);
            if (o == ExifInterface.ORIENTATION_ROTATE_90) degrees = 90;
            else if (o == ExifInterface.ORIENTATION_ROTATE_180) degrees = 180;
            else if (o == ExifInterface.ORIENTATION_ROTATE_270) degrees = 270;
        }
        BitmapFactory.Options bounds = new BitmapFactory.Options();
        bounds.inJustDecodeBounds = true;
        BitmapFactory.decodeByteArray(raw, 0, raw.length, bounds);
        int longEdge = Math.max(bounds.outWidth, bounds.outHeight);
        BitmapFactory.Options o = new BitmapFactory.Options();
        o.inSampleSize = 1;
        while (longEdge / (o.inSampleSize * 2) >= 1600) o.inSampleSize *= 2;
        Bitmap bmp = BitmapFactory.decodeByteArray(raw, 0, raw.length, o);
        Bitmap full = scaleRotate(bmp, 1600, degrees);
        Bitmap small = scaleRotate(full, 320, 0);
        byte[][] out = {jpeg(full, 85), jpeg(small, 75)};
        if (small != full) small.recycle();
        if (full != bmp) full.recycle();
        bmp.recycle();
        return out;
    }

    private static Bitmap scaleRotate(Bitmap b, int maxLong, int degrees) {
        float s = Math.min(1f, maxLong / (float) Math.max(b.getWidth(),
                b.getHeight()));
        if (s >= 1f && degrees == 0) return b;
        Matrix m = new Matrix();
        m.postScale(s, s);
        if (degrees != 0) m.postRotate(degrees);
        return Bitmap.createBitmap(b, 0, 0, b.getWidth(), b.getHeight(), m,
                true);
    }

    private static byte[] jpeg(Bitmap b, int q) {
        ByteArrayOutputStream bos = new ByteArrayOutputStream();
        b.compress(Bitmap.CompressFormat.JPEG, q, bos);
        return bos.toByteArray();
    }

    private File queueDir() {
        File d = new File(getFilesDir(), "boxq");
        if (!d.exists()) d.mkdirs();
        return d;
    }

    private static void write(File f, byte[] data) throws Exception {
        try (FileOutputStream out = new FileOutputStream(f)) {
            out.write(data);
        }
    }

    // ===================================================================
    // Upload queue
    /** Shots left from last time (the app closed before they uploaded). */
    private void loadQueued() {
        File[] metas = queueDir().listFiles((d, n) -> n.endsWith(".json"));
        if (metas == null) return;
        Arrays.sort(metas, (a, b) -> b.getName().compareTo(a.getName()));
        for (File m : metas) {
            try {
                JSONObject meta = new JSONObject(new String(
                        Files.readAllBytes(m.toPath()), StandardCharsets.UTF_8));
                Pending p = new Pending();
                p.uid = meta.getString("uid");
                p.pinSku = meta.isNull("pin_sku") ? null
                        : meta.optString("pin_sku");
                File t = new File(queueDir(), p.uid + "_t.jpg");
                p.thumb = BitmapFactory.decodeFile(t.getPath());
                pending.add(p);
            } catch (Exception ignored) {
            }
        }
    }

    private boolean draining;

    private void drainUploads() {
        if (draining) return;
        draining = true;
        uploadExec.execute(this::drainLoop);
    }

    private void drainLoop() {
        while (true) {
            File[] metas = queueDir().listFiles((d, n) -> n.endsWith(".json"));
            if (metas == null || metas.length == 0) break;
            Arrays.sort(metas, (a, b) -> a.getName().compareTo(b.getName()));
            File metaFile = metas[0];
            String uid = metaFile.getName().replace(".json", "");
            File img = new File(queueDir(), uid + ".jpg");
            File th = new File(queueDir(), uid + "_t.jpg");
            try {
                JSONObject meta = new JSONObject(new String(
                        Files.readAllBytes(metaFile.toPath()),
                        StandardCharsets.UTF_8));
                JSONObject body = new JSONObject()
                        .put("uid", uid)
                        .put("image_b64", Base64.encodeToString(
                                Files.readAllBytes(img.toPath()), Base64.NO_WRAP))
                        .put("thumb_b64", Base64.encodeToString(
                                Files.readAllBytes(th.toPath()), Base64.NO_WRAP))
                        .put("new_box", meta.optBoolean("new_box", true))
                        .put("worker", meta.optString("worker", ""));
                int b = meta.optInt("batch_id", -1);
                if (b >= 0) body.put("batch_id", b);
                if (!meta.isNull("pin_sku")) {
                    body.put("pin_sku", meta.optString("pin_sku"));
                    body.put("pin_auto", meta.optBoolean("pin_auto", false));
                }
                JSONObject r = api("POST", "/api/boxphotos", body);
                JSONObject photo = r.getJSONObject("photo");
                metaFile.delete();
                img.delete();
                th.delete();
                ui.post(() -> uploaded(uid, photo));
            } catch (Exception e) {
                String msg = String.valueOf(e.getMessage());
                boolean fatal = msg.contains("isn't a JPEG")
                        || msg.contains("too large")
                        || msg.contains("didn't arrive intact");
                if (fatal) {
                    metaFile.delete();
                    img.delete();
                    th.delete();
                    ui.post(() -> {
                        Pending p = pendingByUid(uid);
                        pending.remove(p);
                        toast("A photo couldn't be used: " + msg
                                + " Take it again.", null, null);
                        paintCamera();
                        renderPanel();
                    });
                    continue;
                }
                ui.post(() -> {
                    Pending p = pendingByUid(uid);
                    if (p != null) p.waiting = true;
                    paintCamera();
                    renderPanel();
                });
                if (destroyed) break;
                try {
                    Thread.sleep(10000);
                } catch (InterruptedException ie) {
                    break;
                }
            }
        }
        ui.post(() -> {
            draining = false;
            // A shot queued while this loop was finishing up.
            File[] left = queueDir().listFiles((d, n) -> n.endsWith(".json"));
            if (!destroyed && left != null && left.length > 0) drainUploads();
        });
    }

    private void uploaded(String uid, JSONObject photo) {
        Pending p = pendingByUid(uid);
        if (p != null) {
            pending.remove(p);
            if (p.thumb != null) thumbs.put("p:" + photo.optInt("id"), p.thumb);
        }
        absorbTitles(photo);
        // Replace or add, newest first.
        List<JSONObject> list = new ArrayList<>();
        list.add(photo);
        for (JSONObject x : photos) {
            if (x.optInt("id") != photo.optInt("id")) list.add(x);
        }
        photos = list;
        String s = photo.optString("status");
        Box b = boxOf.get(uid);
        if ("auto".equals(s) || "confirmed".equals(s)) {
            lastFiledSku = photo.optString("sku");
            if (b != null && b.sku == null) {
                b.sku = photo.optString("sku");
                fileBoxMates(b);
            }
        } else if (b != null && b.sku != null) {
            // This box was already read: follow it.
            fileQuietly(photo.optInt("id"), b.sku);
        }
        paintCamera();
        renderPanel();
    }

    /** File every unsorted photo of the box into its SKU, as "auto". */
    private void fileBoxMates(Box b) {
        for (String u : b.uids) {
            JSONObject ph = photoByUid(u);
            if (ph == null) continue; // still uploading: follows on arrival
            String st = ph.optString("status");
            if ("ask".equals(st) || "none".equals(st)) {
                fileQuietly(ph.optInt("id"), b.sku);
            }
        }
    }

    private void fileQuietly(int photoId, String sku) {
        netExec.execute(() -> {
            try {
                api("POST", "/api/boxphotos/" + photoId + "/file",
                        workerBody().put("sku", sku).put("auto", true));
                ui.post(this::refresh);
            } catch (Exception ignored) {
            }
        });
    }

    private void undoLast() {
        if (lastUid == null) return;
        String uid = lastUid;
        Box b = boxOf.remove(uid);
        if (b != null) {
            b.uids.remove(uid);
            if (b.shots > 0) b.shots--;
        }
        Pending p = pendingByUid(uid);
        if (p != null) {
            new File(queueDir(), uid + ".json").delete();
            new File(queueDir(), uid + ".jpg").delete();
            new File(queueDir(), uid + "_t.jpg").delete();
            pending.remove(p);
            lastUid = null;
            paintCamera();
            toast("Photo removed", null, null);
            return;
        }
        JSONObject ph = photoByUid(uid);
        if (ph == null) return;
        int id = ph.optInt("id");
        lastUid = null;
        photoAction("/api/boxphotos/" + id + "/delete", new JSONObject(),
                "Photo deleted", "Undo", () -> photoAction(
                        "/api/boxphotos/" + id + "/restore", new JSONObject(),
                        "Photo restored", null, null));
    }

    // ===================================================================
    // Server calls
    private JSONObject api(String method, String path, JSONObject body)
            throws Exception {
        String server = prefs.getString("server", DEFAULT_SERVER)
                .replaceAll("/+$", "");
        String key = prefs.getString("key", "");
        if (key.isEmpty()) {
            throw new Exception("Station key not set (Settings > Connection).");
        }
        HttpURLConnection conn = (HttpURLConnection)
                new URL(server + path).openConnection();
        conn.setConnectTimeout(10000);
        conn.setReadTimeout(45000);
        conn.setRequestMethod(method);
        conn.setRequestProperty("X-Station-Key", key);
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
        String txt = in == null ? "" : new String(readAll(in),
                StandardCharsets.UTF_8);
        conn.disconnect();
        if (code >= 400) {
            String detail = "HTTP " + code;
            try {
                detail = new JSONObject(txt).optString("detail", detail);
            } catch (Exception ignored) {
            }
            throw new Exception(detail);
        }
        return txt.isEmpty() ? new JSONObject() : new JSONObject(txt);
    }

    private byte[] apiBytes(String path) throws Exception {
        String server = prefs.getString("server", DEFAULT_SERVER)
                .replaceAll("/+$", "");
        HttpURLConnection conn = (HttpURLConnection)
                new URL(server + path).openConnection();
        conn.setConnectTimeout(10000);
        conn.setReadTimeout(30000);
        conn.setRequestProperty("X-Station-Key", prefs.getString("key", ""));
        int code = conn.getResponseCode();
        if (code >= 400) {
            conn.disconnect();
            throw new Exception("HTTP " + code);
        }
        byte[] data = readAll(conn.getInputStream());
        conn.disconnect();
        return data;
    }

    private static byte[] readAll(InputStream in) throws Exception {
        ByteArrayOutputStream bos = new ByteArrayOutputStream();
        byte[] buf = new byte[16384];
        int n;
        while ((n = in.read(buf)) > 0) bos.write(buf, 0, n);
        in.close();
        return bos.toByteArray();
    }

    private void loadThumb(ImageView iv, JSONObject photo) {
        final String key = "p:" + photo.optInt("id");
        iv.setTag(key);
        Bitmap hit = thumbs.get(key);
        if (hit == null) hit = thumbs.get("u:" + photo.optString("uid"));
        if (hit != null) {
            iv.setImageBitmap(hit);
            return;
        }
        iv.setImageDrawable(null);
        thumbExec.execute(() -> {
            try {
                byte[] d = apiBytes("/api/boxphotos/" + photo.optInt("id")
                        + "/thumb");
                Bitmap b = BitmapFactory.decodeByteArray(d, 0, d.length);
                if (b == null) return;
                ui.post(() -> {
                    thumbs.put(key, b);
                    if (key.equals(iv.getTag())) iv.setImageBitmap(b);
                });
            } catch (Exception ignored) {
            }
        });
    }

    /** POST, then reload the shipment and say what happened. */
    private void photoAction(String path, JSONObject body, String done,
                             String undoLabel, Runnable undo) {
        netExec.execute(() -> {
            try {
                api("POST", path, body);
                ui.post(() -> {
                    toast(done, undoLabel, undo);
                    refresh();
                });
            } catch (Exception e) {
                ui.post(() -> toast("That didn't save: " + e.getMessage(),
                        null, null));
            }
        });
    }

    private JSONObject workerBody() {
        try {
            return new JSONObject().put("worker",
                    prefs.getString("worker_name", ""));
        } catch (Exception e) {
            return new JSONObject();
        }
    }

    private void fileInto(int photoId, String sku, String title) {
        try {
            if (title != null) skuTitles.put(sku.toUpperCase(Locale.US), title);
            JSONObject b = workerBody().put("sku", sku);
            photoAction("/api/boxphotos/" + photoId + "/file", b,
                    "Filed to " + sku, "Undo", () -> photoAction(
                            "/api/boxphotos/" + photoId + "/unfile",
                            new JSONObject(), "Back in Incoming", null, null));
            lastFiledSku = sku;
            JSONObject ph = photoById(photoId);
            Box bx = ph == null ? null : boxOf.get(ph.optString("uid"));
            if (bx != null && bx.sku == null) {
                bx.sku = sku;
                for (String u : bx.uids) {
                    JSONObject mate = photoByUid(u);
                    if (mate == null || mate.optInt("id") == photoId) continue;
                    String st = mate.optString("status");
                    if ("ask".equals(st) || "none".equals(st)) {
                        fileQuietly(mate.optInt("id"), sku);
                    }
                }
            }
        } catch (Exception ignored) {
        }
    }

    // ===================================================================
    // Toast with an optional action
    private void toast(String msg, String action, Runnable onAction) {
        toastText.setText(msg);
        if (action != null && onAction != null) {
            toastAction.setText(action);
            toastAction.setVisibility(View.VISIBLE);
            toastAction.setOnClickListener(v -> {
                toastBar.setVisibility(View.GONE);
                onAction.run();
            });
        } else {
            toastAction.setVisibility(View.GONE);
        }
        toastBar.setVisibility(View.VISIBLE);
        toastBar.bringToFront();
        if (toastHide != null) ui.removeCallbacks(toastHide);
        toastHide = () -> toastBar.setVisibility(View.GONE);
        ui.postDelayed(toastHide, action != null ? 5000 : 3000);
    }

    // ===================================================================
    // Panels
    private void openPanel(String which, String sku) {
        if (!which.equals(panel) || (sku != null && !sku.equals(panelSku))) {
            selected.clear();
        }
        panel = which;
        panelSku = sku;
        panelHost.setVisibility(View.VISIBLE);
        renderPanel();
        refresh();
    }

    private void closePanel() {
        panel = null;
        panelSku = null;
        selected.clear();
        panelHost.removeAllViews();
        panelHost.setVisibility(View.GONE);
        paintCamera();
    }

    private void renderPanel() {
        if (panel == null) return;
        View v;
        switch (panel) {
            case "incoming":
                v = buildIncoming();
                break;
            case "folder":
                v = buildFolder(panelSku);
                break;
            default:
                v = buildFolders();
                break;
        }
        panelHost.removeAllViews();
        panelHost.addView(v, new FrameLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT));
    }

    /** Header with a back arrow, title and subtitle. */
    private LinearLayout panelHeader(String title, String sub, boolean monoTitle,
                                     Runnable back) {
        LinearLayout h = row();
        h.setPadding(dp(4), dp(8), dp(16), dp(8));
        Button b = button("‹", Color.TRANSPARENT, TEXT, 0);
        b.setTextSize(26);
        b.setPadding(0, 0, 0, dp(4));
        b.setContentDescription("Back");
        b.setOnClickListener(v -> back.run());
        h.addView(b, lp(dp(48), dp(48)));
        LinearLayout t = col();
        t.addView(monoTitle ? mono(title, 20, TEXT) : text(title, 19, TEXT, true));
        if (sub != null) {
            TextView s = text(sub, 12, MUTED, false);
            s.setSingleLine(true);
            s.setEllipsize(android.text.TextUtils.TruncateAt.END);
            t.addView(s);
        }
        h.addView(t, weight1());
        return h;
    }

    private ImageView thumbView(JSONObject photo, int size) {
        ImageView iv = new ImageView(this);
        iv.setScaleType(ImageView.ScaleType.CENTER_CROP);
        iv.setBackground(bg(KRAFT, 8, 0));
        iv.setClipToOutline(true);
        iv.setMinimumWidth(dp(size));
        iv.setMinimumHeight(dp(size));
        if (photo != null) loadThumb(iv, photo);
        else iv.setBackground(bg(CHIP, 8, 0));
        return iv;
    }

    // ---- Incoming ---------------------------------------------------------
    private View buildIncoming() {
        LinearLayout page = col();
        List<JSONObject> need = incoming();
        int total = need.size() + pending.size();
        String sub = total == 0 ? "Nothing waiting"
                : plural(total, "photo", "photos")
                + (pending.isEmpty() ? "" : " · " + pending.size() + " still reading");
        page.addView(panelHeader("Incoming", sub, false, this::closePanel));
        ScrollView sv = new ScrollView(this);
        LinearLayout list = col();
        list.setPadding(dp(12), dp(4), dp(12), dp(16));
        sv.addView(list);
        for (Pending p : pending) {
            LinearLayout card = row();
            card.setBackground(bg(CARD, 12, 0));
            card.setPadding(dp(12), dp(12), dp(12), dp(12));
            ImageView iv = new ImageView(this);
            iv.setScaleType(ImageView.ScaleType.CENTER_CROP);
            iv.setClipToOutline(true);
            iv.setBackground(bg(CHIP, 8, 0));
            iv.setImageBitmap(p.thumb);
            card.addView(iv, lp(dp(64), dp(64)));
            LinearLayout t = col();
            t.setPadding(dp(12), 0, 0, 0);
            t.addView(text(p.waiting ? "Waiting for the server"
                    : "Reading the box", 15, TEXT, true));
            t.addView(text(p.waiting ? "It retries every 10 seconds"
                    : (p.pinSku != null ? "Going to " + p.pinSku : "Just now"),
                    12, MUTED, false));
            if (!p.waiting) {
                ProgressBar bar = new ProgressBar(this, null,
                        android.R.attr.progressBarStyleHorizontal);
                bar.setIndeterminate(true);
                t.addView(bar, fillW());
            }
            card.addView(t, weight1());
            addCard(list, card);
        }
        for (JSONObject ph : need) addCard(list, incomingCard(ph));
        if (total == 0) {
            TextView none = text("Nothing needs you. Photos the reader is "
                    + "sure about file themselves.", 14, MUTED, false);
            none.setPadding(dp(6), dp(20), dp(6), 0);
            list.addView(none);
        } else {
            TextView foot = text("Anything you tap here counts as confirmed. "
                    + "Confident reads skip this list.", 13, MUTED, false);
            foot.setPadding(dp(4), dp(8), dp(4), 0);
            list.addView(foot);
        }
        page.addView(sv, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));
        return page;
    }

    private void addCard(LinearLayout list, View card) {
        LinearLayout.LayoutParams l = fillW();
        l.bottomMargin = dp(10);
        list.addView(card, l);
    }

    private View incomingCard(JSONObject ph) {
        final int id = ph.optInt("id");
        LinearLayout card = col();
        card.setBackground(bg(CARD, 12, 0));
        card.setPadding(dp(12), dp(12), dp(12), dp(12));
        LinearLayout top = row();
        top.addView(thumbView(ph, 64), lp(dp(64), dp(64)));
        LinearLayout t = col();
        t.setPadding(dp(12), 0, 0, 0);
        TextView cap = text("READ ON THE BOX", 11, MUTED, true);
        cap.setLetterSpacing(0.07f);
        t.addView(cap);
        String read = ph.optString("ocr_text", "").replace("\n", " · ");
        if (read.isEmpty()) read = "Nothing readable";
        TextView rt = mono(read, 15, TEXT);
        rt.setMaxLines(2);
        rt.setEllipsize(android.text.TextUtils.TruncateAt.END);
        t.addView(rt);
        JSONArray g = ph.optJSONArray("guesses");
        boolean ask = "ask".equals(ph.optString("status"))
                && g != null && g.length() > 0;
        String err = ph.optString("ocr_error", "");
        TextView note = text(ask ? (g.length() > 1 ? "Not sure which one. "
                + "Pick it." : "Close, but not certain. Check it.")
                : (!err.isEmpty() && !"null".equals(err) ? err
                : "No SKU found. The sticker may be on another side."),
                12, ask ? MUTED : WARN, false);
        t.addView(note);
        top.addView(t, weight1());
        card.addView(top);
        if (ask) {
            for (int i = 0; i < g.length(); i++) {
                JSONObject o = g.optJSONObject(i);
                final String sku = o.optString("sku");
                final String title = o.optString("title");
                LinearLayout gb = row();
                gb.setPadding(dp(12), dp(8), dp(12), dp(8));
                gb.setMinimumHeight(dp(48));
                gb.setBackground(bg(i == 0 ? SOFT : BG, 10,
                        i == 0 ? BLUE : LINE));
                TextView s = mono(sku, 14, TEXT);
                gb.addView(s, lp(dp(96), ViewGroup.LayoutParams.WRAP_CONTENT));
                TextView tt = text(title, 13, TEXT, false);
                tt.setMaxLines(2);
                tt.setEllipsize(android.text.TextUtils.TruncateAt.END);
                gb.addView(tt, weight1());
                if (i == 0) {
                    TextView best = text("Best match", 12, BLUE_TEXT, true);
                    best.setPadding(dp(8), 0, 0, 0);
                    gb.addView(best);
                }
                gb.setOnClickListener(v -> fileInto(id, sku, title));
                LinearLayout.LayoutParams gl = fillW();
                gl.topMargin = dp(8);
                card.addView(gb, gl);
            }
        }
        LinearLayout acts = row();
        Button other = ghost(ask ? "Another folder" : "Pick a folder");
        other.setTextColor(BLUE_TEXT);
        other.setOnClickListener(v -> pickFolder("File this photo",
                (sku, title) -> fileInto(id, sku, title)));
        acts.addView(other, weight1());
        Button del = ghost("Delete");
        del.setTextColor(BAD_TEXT);
        del.setBackground(bg(Color.TRANSPARENT, 10, BAD_LINE));
        del.setOnClickListener(v -> photoAction("/api/boxphotos/" + id
                + "/delete", new JSONObject(), "Photo deleted", "Undo",
                () -> photoAction("/api/boxphotos/" + id + "/restore",
                        new JSONObject(), "Photo restored", null, null)));
        LinearLayout.LayoutParams dlp = wrap();
        dlp.leftMargin = dp(8);
        acts.addView(del, dlp);
        LinearLayout.LayoutParams al = fillW();
        al.topMargin = dp(10);
        card.addView(acts, al);
        return card;
    }

    // ---- Folders ----------------------------------------------------------
    private View buildFolders() {
        LinearLayout page = col();
        LinkedHashMap<String, List<JSONObject>> fs = folders();
        page.addView(panelHeader("Folders", plural(fs.size(), "folder",
                "folders") + " in this shipment", false, this::closePanel));

        LinearLayout sbox = row();
        sbox.setBackground(bg(CARD, 10, LINE));
        sbox.setPadding(dp(12), 0, dp(12), 0);
        EditText search = new EditText(this);
        search.setHint("Search SKU or name, or scan a barcode");
        search.setHintTextColor(MUTED);
        search.setTextColor(TEXT);
        search.setTextSize(14);
        search.setSingleLine(true);
        search.setBackground(null);
        search.setText(folderQuery);
        search.setSelection(folderQuery.length());
        sbox.addView(search, weight1());
        LinearLayout.LayoutParams sl = fillW();
        sl.setMargins(dp(12), 0, dp(12), dp(8));
        page.addView(sbox, sl);

        ScrollView sv = new ScrollView(this);
        LinearLayout list = col();
        list.setPadding(dp(12), 0, dp(12), dp(16));
        sv.addView(list);
        page.addView(sv, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));
        fillFolderList(list, fs);

        search.addTextChangedListener(new TextWatcher() {
            private Runnable pendingSearch;

            @Override
            public void beforeTextChanged(CharSequence s, int a, int b, int c) {
            }

            @Override
            public void onTextChanged(CharSequence s, int a, int b, int c) {
            }

            @Override
            public void afterTextChanged(Editable e) {
                folderQuery = e.toString().trim();
                folderSearch = null;
                list.removeAllViews();
                fillFolderList(list, folders());
                if (pendingSearch != null) ui.removeCallbacks(pendingSearch);
                if (folderQuery.length() < 2) return;
                final String q = folderQuery;
                pendingSearch = () -> searchCatalog(q, results -> {
                    if (!q.equals(folderQuery)) return;
                    folderSearch = results;
                    list.removeAllViews();
                    fillFolderList(list, folders());
                });
                ui.postDelayed(pendingSearch, 300);
            }
        });

        int boxes = 0;
        for (List<JSONObject> l : fs.values()) boxes += defaultBoxes(l);
        LinearLayout bar = row();
        bar.setPadding(dp(12), dp(10), dp(12), dp(14));
        Button send = primary(fs.isEmpty() ? "Send to sorter"
                : "Send to sorter (" + plural(boxes, "box", "boxes") + ")");
        send.setEnabled(!fs.isEmpty());
        send.setAlpha(fs.isEmpty() ? 0.5f : 1f);
        send.setOnClickListener(v -> showSend());
        bar.addView(send, weight1());
        page.addView(bar, fillW());
        return page;
    }

    private boolean matchesQuery(String sku, String title) {
        if (folderQuery.isEmpty()) return true;
        String n = norm(folderQuery);
        if (!n.isEmpty() && norm(sku).contains(n)) return true;
        String t = title == null ? "" : title.toLowerCase(Locale.US);
        for (String w : folderQuery.toLowerCase(Locale.US).split("\\s+")) {
            if (!w.isEmpty() && !t.contains(w)) return false;
        }
        return true;
    }

    private void fillFolderList(LinearLayout list,
                                LinkedHashMap<String, List<JSONObject>> fs) {
        Set<String> shown = new HashSet<>();
        JSONArray products = batch == null ? null
                : batch.optJSONArray("products");
        if (products != null && products.length() > 0) {
            int done = 0;
            for (int i = 0; i < products.length(); i++) {
                if (fs.containsKey(products.optJSONObject(i).optString("sku"))) {
                    done++;
                }
            }
            LinearLayout cap = row();
            cap.setPadding(dp(4), dp(6), dp(4), dp(8));
            TextView c1 = text(("This batch · " + batch.optString("label"))
                    .toUpperCase(Locale.US), 11, MUTED, true);
            c1.setLetterSpacing(0.07f);
            cap.addView(c1, weight1());
            cap.addView(text(done + " of " + products.length()
                    + " photographed", 12, MUTED, false));
            list.addView(cap, fillW());
            ProgressBar pb = new ProgressBar(this, null,
                    android.R.attr.progressBarStyleHorizontal);
            pb.setMax(products.length());
            pb.setProgress(done);
            pb.setProgressTintList(android.content.res.ColorStateList.valueOf(OK));
            LinearLayout.LayoutParams pl = fillW();
            pl.bottomMargin = dp(8);
            list.addView(pb, pl);
            LinearLayout group = col();
            group.setBackground(bg(CARD, 12, 0));
            int count = 0;
            for (int i = 0; i < products.length(); i++) {
                JSONObject p = products.optJSONObject(i);
                String sku = p.optString("sku");
                if (!matchesQuery(sku, p.optString("title"))) continue;
                shown.add(sku.toUpperCase(Locale.US));
                group.addView(folderRow(sku, p.optString("title"), fs.get(sku)));
                count++;
            }
            if (count > 0) list.addView(group, fillW());
        }
        LinearLayout other = col();
        other.setBackground(bg(CARD, 12, 0));
        int n = 0;
        for (Map.Entry<String, List<JSONObject>> e : fs.entrySet()) {
            if (shown.contains(e.getKey().toUpperCase(Locale.US))) continue;
            if (!matchesQuery(e.getKey(), titleFor(e.getKey()))) continue;
            shown.add(e.getKey().toUpperCase(Locale.US));
            other.addView(folderRow(e.getKey(), titleFor(e.getKey()),
                    e.getValue()));
            n++;
        }
        if (n > 0) {
            TextView cap = text(products != null && products.length() > 0
                    ? "OTHER FOLDERS" : "FOLDERS", 11, MUTED, true);
            cap.setLetterSpacing(0.07f);
            cap.setPadding(dp(4), dp(16), dp(4), dp(8));
            list.addView(cap);
            list.addView(other, fillW());
        }
        if (folderSearch != null && folderSearch.length() > 0) {
            LinearLayout found = col();
            found.setBackground(bg(CARD, 12, 0));
            int m = 0;
            for (int i = 0; i < folderSearch.length(); i++) {
                JSONObject r = folderSearch.optJSONObject(i);
                String sku = r.optString("sku");
                if (shown.contains(sku.toUpperCase(Locale.US))) continue;
                skuTitles.put(sku.toUpperCase(Locale.US), r.optString("title"));
                found.addView(folderRow(sku, r.optString("title"), null));
                m++;
            }
            if (m > 0) {
                TextView cap = text("OTHER PRODUCTS", 11, MUTED, true);
                cap.setLetterSpacing(0.07f);
                cap.setPadding(dp(4), dp(16), dp(4), dp(8));
                list.addView(cap);
                list.addView(found, fillW());
            }
        }
        if (fs.isEmpty() && (products == null || products.length() == 0)
                && folderQuery.isEmpty()) {
            TextView none = text("No folders yet. Photos the reader files, "
                    + "and photos you sort, land here by SKU.", 14, MUTED, false);
            none.setPadding(dp(6), dp(20), dp(6), 0);
            list.addView(none);
        }
    }

    private View folderRow(String sku, String title, List<JSONObject> ps) {
        int nPhotos = ps == null ? 0 : ps.size();
        int autos = 0;
        if (ps != null) {
            for (JSONObject p : ps) if ("auto".equals(p.optString("status"))) autos++;
        }
        LinearLayout r = row();
        r.setPadding(dp(12), dp(10), dp(12), dp(10));
        r.setMinimumHeight(dp(56));
        r.setBackground(bg(Color.TRANSPARENT, 0, 0));
        ImageView iv = thumbView(nPhotos > 0 ? ps.get(0) : null, 40);
        r.addView(iv, lp(dp(40), dp(40)));
        LinearLayout t = col();
        t.setPadding(dp(12), 0, dp(8), 0);
        t.addView(mono(sku, 15, TEXT));
        TextView tt = text(title == null || "null".equals(title) ? "" : title,
                12, MUTED, false);
        tt.setSingleLine(true);
        tt.setEllipsize(android.text.TextUtils.TruncateAt.END);
        t.addView(tt);
        r.addView(t, weight1());
        LinearLayout right = col();
        right.setGravity(Gravity.END);
        right.addView(text(nPhotos == 0 ? "No photos yet"
                : plural(nPhotos, "photo", "photos"), 13,
                nPhotos == 0 ? MUTED : TEXT, true));
        if (autos > 0) {
            TextView c = chip(autos + " to check", WARN_BG, WARN);
            c.setTextSize(11);
            LinearLayout.LayoutParams cl = wrap();
            cl.topMargin = dp(3);
            cl.gravity = Gravity.END;
            right.addView(c, cl);
        }
        r.addView(right);
        r.setAlpha(nPhotos == 0 ? 0.75f : 1f);
        r.setOnClickListener(v -> openPanel("folder", sku));
        return r;
    }

    // ---- One folder -------------------------------------------------------
    private View buildFolder(String sku) {
        LinearLayout page = col();
        List<JSONObject> ps = folderPhotos(sku);
        page.addView(panelHeader(sku, titleFor(sku), true,
                () -> openPanel("folders", null)));
        int boxes = 0, autos = 0;
        for (JSONObject p : ps) {
            if (p.optBoolean("new_box", true)) boxes++;
            if ("auto".equals(p.optString("status"))) autos++;
        }
        LinearLayout chips = row();
        chips.setPadding(dp(16), 0, dp(16), dp(10));
        chips.addView(chip("Photos: " + ps.size(), CHIP, TEXT));
        TextView bc = chip("Boxes: " + boxes, CHIP, TEXT);
        LinearLayout.LayoutParams bcl = wrap();
        bcl.leftMargin = dp(6);
        chips.addView(bc, bcl);
        page.addView(chips, fillW());

        ScrollView sv = new ScrollView(this);
        LinearLayout body = col();
        body.setPadding(dp(12), 0, dp(12), dp(12));
        sv.addView(body);

        JSONObject seen = null;
        int readCount = 0;
        for (JSONObject p : ps) {
            String t = p.optString("ocr_text", "");
            if (t.isEmpty()) continue;
            if (seen == null) seen = p;
            if (norm(t).contains(norm(sku))) readCount++;
        }
        if (seen != null) {
            LinearLayout card = col();
            card.setBackground(bg(CARD, 12, 0));
            card.setPadding(dp(12), dp(12), dp(12), dp(12));
            LinearLayout capRow = row();
            TextView cap = text("WHAT THE READER SAW", 11, MUTED, true);
            cap.setLetterSpacing(0.07f);
            capRow.addView(cap, weight1());
            capRow.addView(text("SKU read on " + readCount + " of "
                    + ps.size(), 12, MUTED, false));
            card.addView(capRow);
            FlowLayout words = new FlowLayout(this, dp(6));
            for (String w : seen.optString("ocr_text").split("[\\n ]+")) {
                if (w.trim().isEmpty()) continue;
                boolean hit = norm(w).equals(norm(sku));
                TextView c = mono(w, 13, hit ? OK_TEXT : TEXT);
                c.setBackground(bg(hit ? OK_BG : CHIP, 6, 0));
                c.setPadding(dp(8), dp(3), dp(8), dp(3));
                words.addView(c);
            }
            LinearLayout.LayoutParams wl = fillW();
            wl.topMargin = dp(8);
            card.addView(words, wl);
            LinearLayout.LayoutParams cl = fillW();
            cl.bottomMargin = dp(12);
            body.addView(card, cl);
        }

        GridLayout grid = new GridLayout(this);
        grid.setColumnCount(3);
        int screenW = getResources().getDisplayMetrics().widthPixels;
        int tile = (screenW - dp(24) - dp(24)) / 3;
        for (JSONObject p : ps) {
            final int id = p.optInt("id");
            boolean sel = selected.contains(id);
            boolean auto = "auto".equals(p.optString("status"));
            FrameLayout cell = new FrameLayout(this);
            cell.setBackground(bg(KRAFT, 10, 0));
            cell.setClipToOutline(true);
            ImageView iv = new ImageView(this);
            iv.setScaleType(ImageView.ScaleType.CENTER_CROP);
            loadThumb(iv, p);
            cell.addView(iv, new FrameLayout.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT,
                    ViewGroup.LayoutParams.MATCH_PARENT));
            TextView badge = auto ? chip("AUTO", WARN_BG, WARN)
                    : chip("✓", OK_BG, OK_TEXT);
            badge.setTextSize(10);
            FrameLayout.LayoutParams bl = new FrameLayout.LayoutParams(
                    ViewGroup.LayoutParams.WRAP_CONTENT,
                    ViewGroup.LayoutParams.WRAP_CONTENT,
                    Gravity.TOP | Gravity.START);
            bl.setMargins(dp(6), dp(6), 0, 0);
            cell.addView(badge, bl);
            if (!p.optBoolean("new_box", true)) {
                TextView ang = chip("Angle", 0xCC16181A, TEXT);
                ang.setTextSize(10);
                FrameLayout.LayoutParams al = new FrameLayout.LayoutParams(
                        ViewGroup.LayoutParams.WRAP_CONTENT,
                        ViewGroup.LayoutParams.WRAP_CONTENT,
                        Gravity.BOTTOM | Gravity.START);
                al.setMargins(dp(6), 0, 0, dp(6));
                cell.addView(ang, al);
            }
            if (sel) {
                View ring = new View(this);
                GradientDrawable rd = new GradientDrawable();
                rd.setColor(0x332F7DE1);
                rd.setStroke(dp(3), BLUE);
                rd.setCornerRadius(dp(10));
                ring.setBackground(rd);
                cell.addView(ring, new FrameLayout.LayoutParams(
                        ViewGroup.LayoutParams.MATCH_PARENT,
                        ViewGroup.LayoutParams.MATCH_PARENT));
                TextView tick = chip("✓", BLUE_FILL, Color.WHITE);
                FrameLayout.LayoutParams tl = new FrameLayout.LayoutParams(
                        ViewGroup.LayoutParams.WRAP_CONTENT,
                        ViewGroup.LayoutParams.WRAP_CONTENT,
                        Gravity.TOP | Gravity.END);
                tl.setMargins(0, dp(6), dp(6), 0);
                cell.addView(tick, tl);
            }
            cell.setOnClickListener(v -> {
                if (!selected.remove(id)) selected.add(id);
                renderPanel();
            });
            cell.setOnLongClickListener(v -> {
                showFull(p);
                return true;
            });
            GridLayout.LayoutParams gl = new GridLayout.LayoutParams();
            gl.width = tile;
            gl.height = tile;
            gl.setMargins(0, 0, dp(8), dp(8));
            grid.addView(cell, gl);
        }
        body.addView(grid);
        TextView help = text("Tap photos to select them for moving or "
                + "deleting; hold one to see it big. AUTO photos were sorted "
                + "by the reader and aren't used for training until you "
                + "confirm them.", 13, MUTED, false);
        help.setPadding(dp(2), dp(4), dp(2), 0);
        body.addView(help);
        page.addView(sv, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, 0, 1f));

        LinearLayout bar = row();
        bar.setPadding(dp(12), dp(10), dp(12), dp(14));
        if (!selected.isEmpty()) {
            final List<Integer> ids = new ArrayList<>(selected);
            bar.addView(text(selected.size() + " selected", 14, TEXT, true),
                    weight1());
            Button move = ghost("Move");
            move.setOnClickListener(v -> pickFolder("Move "
                    + plural(ids.size(), "photo", "photos"), (to, title) -> {
                selected.clear();
                for (int id : ids) fileInto(id, to, title);
            }));
            bar.addView(move);
            Button del = ghost("Delete");
            del.setTextColor(BAD_TEXT);
            del.setBackground(bg(Color.TRANSPARENT, 10, BAD_LINE));
            del.setOnClickListener(v -> {
                selected.clear();
                for (int id : ids) {
                    photoAction("/api/boxphotos/" + id + "/delete",
                            new JSONObject(), plural(ids.size(), "photo",
                                    "photos") + " deleted", null, null);
                }
            });
            LinearLayout.LayoutParams dl = wrap();
            dl.leftMargin = dp(8);
            bar.addView(del, dl);
        } else {
            Button keep = ghost("Shoot into this folder");
            keep.setOnClickListener(v -> {
                pinSku = sku;
                closePanel();
                toast("Photos now go straight to " + sku, null, null);
            });
            bar.addView(keep, autos > 0 ? wrap() : weight1());
            if (autos > 0) {
                final List<Integer> autoIds = new ArrayList<>();
                for (JSONObject p : ps) {
                    if ("auto".equals(p.optString("status"))) {
                        autoIds.add(p.optInt("id"));
                    }
                }
                Button ok = primary("Looks right (" + autos + ")");
                ok.setOnClickListener(v -> {
                    try {
                        JSONObject b = workerBody().put("ids",
                                new JSONArray(autoIds));
                        photoAction("/api/boxphotos/confirm", b,
                                plural(autoIds.size(), "photo", "photos")
                                        + " confirmed", null, null);
                    } catch (Exception ignored) {
                    }
                });
                LinearLayout.LayoutParams ol = weight1();
                ol.leftMargin = dp(8);
                bar.addView(ok, ol);
            }
        }
        page.addView(bar, fillW());
        return page;
    }

    private void showFull(JSONObject p) {
        ImageView iv = new ImageView(this);
        iv.setAdjustViewBounds(true);
        iv.setBackgroundColor(Color.BLACK);
        loadThumb(iv, p);
        AlertDialog d = new AlertDialog.Builder(this,
                android.R.style.Theme_Material_Dialog_Alert)
                .setView(iv)
                .setPositiveButton("Close", null)
                .create();
        d.show();
        thumbExec.execute(() -> {
            try {
                byte[] data = apiBytes("/api/boxphotos/" + p.optInt("id")
                        + "/image");
                Bitmap b = BitmapFactory.decodeByteArray(data, 0, data.length);
                if (b != null) ui.post(() -> iv.setImageBitmap(b));
            } catch (Exception ignored) {
            }
        });
    }

    /** Wraps children onto new lines, for the reader's word chips. */
    private static final class FlowLayout extends ViewGroup {
        private final int gap;

        FlowLayout(Context c, int gap) {
            super(c);
            this.gap = gap;
        }

        @Override
        protected void onMeasure(int wSpec, int hSpec) {
            int maxW = MeasureSpec.getSize(wSpec);
            int x = 0, y = 0, lineH = 0;
            for (int i = 0; i < getChildCount(); i++) {
                View c = getChildAt(i);
                c.measure(MeasureSpec.makeMeasureSpec(maxW, MeasureSpec.AT_MOST),
                        MeasureSpec.makeMeasureSpec(0, MeasureSpec.UNSPECIFIED));
                if (x > 0 && x + c.getMeasuredWidth() > maxW) {
                    x = 0;
                    y += lineH + gap;
                    lineH = 0;
                }
                x += c.getMeasuredWidth() + gap;
                lineH = Math.max(lineH, c.getMeasuredHeight());
            }
            setMeasuredDimension(maxW, y + lineH);
        }

        @Override
        protected void onLayout(boolean changed, int l, int t, int r, int b) {
            int maxW = r - l;
            int x = 0, y = 0, lineH = 0;
            for (int i = 0; i < getChildCount(); i++) {
                View c = getChildAt(i);
                int w = c.getMeasuredWidth(), h = c.getMeasuredHeight();
                if (x > 0 && x + w > maxW) {
                    x = 0;
                    y += lineH + gap;
                    lineH = 0;
                }
                c.layout(x, y, x + w, y + h);
                x += w + gap;
                lineH = Math.max(lineH, h);
            }
        }
    }

    // ---- Folder picker ----------------------------------------------------
    private interface Picked {
        void on(String sku, String title);
    }

    private interface Results {
        void on(JSONArray results);
    }

    private void searchCatalog(String q, Results cb) {
        netExec.execute(() -> {
            try {
                String path = "/api/boxphotos/search?q="
                        + URLEncoder.encode(q, "UTF-8")
                        + (batchId >= 0 ? "&batch_id=" + batchId : "");
                JSONArray r = api("GET", path, null).optJSONArray("results");
                ui.post(() -> cb.on(r == null ? new JSONArray() : r));
            } catch (Exception e) {
                ui.post(() -> cb.on(new JSONArray()));
            }
        });
    }

    private void pickFolder(String title, Picked done) {
        LinearLayout box = col();
        box.setPadding(dp(16), dp(8), dp(16), 0);
        EditText q = new EditText(this);
        q.setHint("Search SKU or name, or scan a barcode");
        q.setSingleLine(true);
        q.setTextSize(15);
        box.addView(q, fillW());
        ScrollView sv = new ScrollView(this);
        LinearLayout list = col();
        sv.addView(list);
        box.addView(sv, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, dp(360)));
        AlertDialog d = new AlertDialog.Builder(this,
                android.R.style.Theme_Material_Dialog_Alert)
                .setTitle(title)
                .setView(box)
                .setNegativeButton("Cancel", null)
                .create();
        final Results show = results -> {
            list.removeAllViews();
            if (results.length() == 0) {
                TextView none = text(q.getText().length() < 2
                        ? "Type at least 2 letters."
                        : "Nothing matches that.", 14, MUTED, false);
                none.setPadding(0, dp(12), 0, 0);
                list.addView(none);
            }
            for (int i = 0; i < results.length(); i++) {
                JSONObject r = results.optJSONObject(i);
                final String sku = r.optString("sku");
                final String t = r.optString("title");
                LinearLayout rr = col();
                rr.setPadding(0, dp(10), 0, dp(10));
                LinearLayout top = row();
                top.addView(mono(sku, 15, TEXT));
                if (r.optBoolean("in_batch")) {
                    TextView c = chip("This batch", SOFT, BLUE_TEXT);
                    c.setTextSize(11);
                    LinearLayout.LayoutParams cl = wrap();
                    cl.leftMargin = dp(8);
                    top.addView(c, cl);
                }
                rr.addView(top);
                TextView tt = text(t, 12, MUTED, false);
                tt.setMaxLines(2);
                rr.addView(tt);
                rr.setOnClickListener(v -> {
                    d.dismiss();
                    done.on(sku, t);
                });
                list.addView(rr, fillW());
            }
        };
        // Start with the folders already in use and the batch's products.
        JSONArray start = new JSONArray();
        Set<String> seen = new HashSet<>();
        for (String sku : folders().keySet()) {
            try {
                start.put(new JSONObject().put("sku", sku)
                        .put("title", titleFor(sku) == null ? "" : titleFor(sku)));
                seen.add(sku.toUpperCase(Locale.US));
            } catch (Exception ignored) {
            }
        }
        JSONArray products = batch == null ? null : batch.optJSONArray("products");
        for (int i = 0; products != null && i < products.length(); i++) {
            JSONObject p = products.optJSONObject(i);
            if (seen.add(p.optString("sku").toUpperCase(Locale.US))) {
                try {
                    start.put(new JSONObject().put("sku", p.optString("sku"))
                            .put("title", p.optString("title"))
                            .put("in_batch", true));
                } catch (Exception ignored) {
                }
            }
        }
        show.on(start);
        q.addTextChangedListener(new TextWatcher() {
            private Runnable pend;

            @Override
            public void beforeTextChanged(CharSequence s, int a, int b, int c) {
            }

            @Override
            public void onTextChanged(CharSequence s, int a, int b, int c) {
            }

            @Override
            public void afterTextChanged(Editable e) {
                if (pend != null) ui.removeCallbacks(pend);
                final String term = e.toString().trim();
                if (term.isEmpty()) {
                    show.on(start);
                    return;
                }
                pend = () -> searchCatalog(term, r -> {
                    if (term.equals(q.getText().toString().trim())) show.on(r);
                });
                ui.postDelayed(pend, 300);
            }
        });
        d.show();
    }

    // ---- Send to sorter ---------------------------------------------------
    /** Boxes a folder counts as by default: every "Read the box" shot is
     *  a box, "Keep in" shots are extra angles; at least one. */
    private static int defaultBoxes(List<JSONObject> ps) {
        int n = 0;
        for (JSONObject p : ps) if (p.optBoolean("new_box", true)) n++;
        return Math.max(1, n);
    }

    private void showSend() {
        LinkedHashMap<String, List<JSONObject>> fs = folders();
        if (fs.isEmpty()) return;
        final LinkedHashMap<String, int[]> qty = new LinkedHashMap<>();
        int autos = 0;
        final List<Integer> ids = new ArrayList<>();
        for (Map.Entry<String, List<JSONObject>> e : fs.entrySet()) {
            qty.put(e.getKey(), new int[]{defaultBoxes(e.getValue())});
            for (JSONObject p : e.getValue()) {
                ids.add(p.optInt("id"));
                if ("auto".equals(p.optString("status"))) autos++;
            }
        }
        LinearLayout box = col();
        box.setPadding(dp(16), dp(4), dp(16), 0);
        TextView intro = text("Check the box count for each product. It "
                + "counts each box you closed with Next box; fix it if one "
                + "was missed.", 13, MUTED, false);
        box.addView(intro);
        if (autos > 0) {
            TextView w = text(plural(autos, "photo is", "photos are")
                    + " still AUTO (not checked). They're included, but "
                    + "aren't used for training until confirmed.", 13, WARN,
                    false);
            w.setPadding(0, dp(8), 0, 0);
            box.addView(w);
        }
        int waiting = incoming().size() + pending.size();
        if (waiting > 0) {
            TextView w = text(plural(waiting, "photo", "photos")
                    + " in Incoming aren't sorted and won't be sent.", 13,
                    WARN, false);
            w.setPadding(0, dp(6), 0, 0);
            box.addView(w);
        }
        ScrollView sv = new ScrollView(this);
        LinearLayout list = col();
        sv.addView(list);
        final TextView total = text("", 14, TEXT, true);
        final Runnable sum = () -> {
            int t = 0, n = 0;
            for (int[] q : qty.values()) {
                t += q[0];
                if (q[0] > 0) n++;
            }
            total.setText(plural(t, "box", "boxes") + " of "
                    + plural(n, "product", "products"));
        };
        for (Map.Entry<String, int[]> e : qty.entrySet()) {
            final int[] q = e.getValue();
            LinearLayout r = row();
            r.setPadding(0, dp(8), 0, dp(8));
            LinearLayout t = col();
            t.addView(mono(e.getKey(), 15, TEXT));
            String ti = titleFor(e.getKey());
            if (ti != null) {
                TextView tt = text(ti, 12, MUTED, false);
                tt.setSingleLine(true);
                tt.setEllipsize(android.text.TextUtils.TruncateAt.END);
                t.addView(tt);
            }
            r.addView(t, weight1());
            final TextView n = mono(String.valueOf(q[0]), 16, TEXT);
            n.setGravity(Gravity.CENTER);
            Button minus = button("−", CHIP, TEXT, 0);
            Button plus = button("+", CHIP, TEXT, 0);
            minus.setOnClickListener(v -> {
                if (q[0] > 0) q[0]--;
                n.setText(String.valueOf(q[0]));
                sum.run();
            });
            plus.setOnClickListener(v -> {
                q[0]++;
                n.setText(String.valueOf(q[0]));
                sum.run();
            });
            r.addView(minus, lp(dp(44), dp(44)));
            r.addView(n, lp(dp(40), ViewGroup.LayoutParams.WRAP_CONTENT));
            r.addView(plus, lp(dp(44), dp(44)));
            list.addView(r, fillW());
        }
        box.addView(sv, new LinearLayout.LayoutParams(
                ViewGroup.LayoutParams.MATCH_PARENT, dp(300)));
        total.setPadding(0, dp(8), 0, dp(4));
        box.addView(total);
        sum.run();
        new AlertDialog.Builder(this, android.R.style.Theme_Material_Dialog_Alert)
                .setTitle("Send to the shipment sorter")
                .setView(box)
                .setNegativeButton("Cancel", null)
                .setPositiveButton("Send", (dlg, w) -> send(qty, ids))
                .show();
    }

    private void send(LinkedHashMap<String, int[]> qty, List<Integer> ids) {
        netExec.execute(() -> {
            try {
                JSONArray items = new JSONArray();
                int boxes = 0;
                for (Map.Entry<String, int[]> e : qty.entrySet()) {
                    if (e.getValue()[0] <= 0) continue;
                    String t = titleFor(e.getKey());
                    JSONObject it = new JSONObject().put("sku", e.getKey())
                            .put("qty", e.getValue()[0]);
                    if (t != null) it.put("title", t.length() > 250
                            ? t.substring(0, 250) : t);
                    items.put(it);
                    boxes += e.getValue()[0];
                }
                if (items.length() == 0) {
                    ui.post(() -> toast("Nothing to send: every count is 0.",
                            null, null));
                    return;
                }
                JSONObject b = workerBody().put("items", items)
                        .put("photo_ids", new JSONArray(ids));
                if (batchId >= 0) b.put("batch_id", batchId);
                api("POST", "/api/boxphotos/send", b);
                final int sent = boxes;
                ui.post(() -> {
                    pinSku = null;
                    lastFiledSku = null;
                    lastUid = null;
                    closePanel();
                    refresh();
                    new AlertDialog.Builder(this,
                            android.R.style.Theme_Material_Dialog_Alert)
                            .setTitle("Sent " + plural(sent, "box", "boxes"))
                            .setMessage("On the web terminal: Batch tab > "
                                    + "Sort a shipment > Load into the "
                                    + "sorter. The folders here start fresh "
                                    + "for the next shipment.")
                            .setPositiveButton("OK", null)
                            .show();
                });
            } catch (Exception e) {
                ui.post(() -> toast("Didn't send: " + e.getMessage(),
                        null, null));
            }
        });
    }
}
