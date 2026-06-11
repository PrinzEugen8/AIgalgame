package com.aigalgame.demo.live2d;

import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.opengl.GLES20;
import android.opengl.GLUtils;

import com.aigalgame.demo.Live2DRenderCommand;
import com.aigalgame.demo.OutfitPlacement;
import com.live2d.sdk.cubism.framework.CubismModelSettingJson;
import com.live2d.sdk.cubism.framework.ICubismModelSetting;
import com.live2d.sdk.cubism.framework.math.CubismMatrix44;
import com.live2d.sdk.cubism.framework.model.CubismModel;
import com.live2d.sdk.cubism.framework.model.CubismModelMultiplyAndScreenColor;
import com.live2d.sdk.cubism.framework.model.CubismUserModel;
import com.live2d.sdk.cubism.framework.rendering.CubismRenderer;
import com.live2d.sdk.cubism.framework.rendering.android.CubismRendererAndroid;

import java.io.IOException;
import java.io.InputStream;
import java.util.HashMap;
import java.util.Locale;
import java.util.Map;

final class NekoLive2DModel extends CubismUserModel {
    private static final int MAX_RUNTIME_TEXTURE_EDGE = 2048;

    private final Context appContext;
    private final Map<String, Integer> parameterIndices = new HashMap<String, Integer>();
    private ICubismModelSetting modelSetting;
    private String modelHomeDirectory = "";
    private float elapsedSeconds;
    private float smoothedMouth;
    private float smoothedLookX;
    private float smoothedLookY;
    private float motionPulse;
    private long lastCommandNonce = Long.MIN_VALUE;

    NekoLive2DModel(Context context) {
        appContext = context.getApplicationContext();
    }

    void loadAssets(String model3Path, int width, int height) {
        int lastSlash = model3Path.lastIndexOf('/');
        modelHomeDirectory = lastSlash >= 0 ? model3Path.substring(0, lastSlash + 1) : "";
        byte[] model3Json = OfficialLive2DRenderer.readAsset(appContext, model3Path);
        if (model3Json == null || model3Json.length == 0) {
            throw new IllegalStateException("model3.json not found: " + model3Path);
        }
        CubismModelSettingJson setting = new CubismModelSettingJson(model3Json);
        if (setting.getJson() == null) {
            throw new IllegalStateException("model3.json parse failed: " + model3Path);
        }
        modelSetting = setting;

        String mocPath = modelSetting.getModelFileName();
        if (mocPath == null || mocPath.isEmpty()) {
            throw new IllegalStateException("Moc entry missing in model3.json");
        }
        byte[] moc = OfficialLive2DRenderer.readAsset(appContext, modelHomeDirectory + mocPath);
        if (moc == null || moc.length == 0) {
            throw new IllegalStateException("MOC3 not found: " + mocPath);
        }
        loadModel(moc);

        String physicsPath = modelSetting.getPhysicsFileName();
        if (physicsPath != null && !physicsPath.isEmpty()) {
            byte[] physicsBytes = OfficialLive2DRenderer.readAsset(appContext, modelHomeDirectory + physicsPath);
            if (physicsBytes != null && physicsBytes.length > 0) {
                loadPhysics(physicsBytes);
            }
        }

        indexParameters();
        CubismRenderer renderer = CubismRendererAndroid.create(Math.max(width, 1), Math.max(height, 1));
        renderer.setModelColor(1.0f, 1.0f, 1.0f, 1.0f);
        setupRenderer(renderer);
        normalizeMultiplyAndScreenColors();
        setupTextures();
        model.saveParameters();
        isInitialized(true);
    }

    void deleteModel() {
        delete();
        parameterIndices.clear();
        isInitialized(false);
    }

    int getDrawableCount() {
        return model == null ? 0 : model.getDrawableCount();
    }

    void configureModelMatrix(
        OutfitPlacement placement,
        int width,
        int height,
        float canvasRatio,
        float displayRatio
    ) {
        if (modelMatrix == null || placement == null) {
            return;
        }
        modelMatrix.loadIdentity();
        if (canvasRatio < displayRatio) {
            modelMatrix.setWidth(2.0f * placement.getScale());
        } else {
            modelMatrix.setHeight(2.0f * placement.getScale());
        }
        float normalizedX = safeDivide(placement.getOffsetX(), Math.max(width, 1)) * 2.0f;
        float normalizedY = -safeDivide(placement.getOffsetY() - placement.getBottomInset(), Math.max(height, 1)) * 2.0f;
        modelMatrix.translateRelative(normalizedX, normalizedY);
    }

    void update(Live2DRenderCommand command, float deltaSeconds) {
        if (model == null) {
            return;
        }
        elapsedSeconds += Math.max(0.0f, deltaSeconds);
        if (command != null && command.getCommandNonce() != lastCommandNonce) {
            lastCommandNonce = command.getCommandNonce();
            motionPulse = 1.0f;
        }
        motionPulse = approach(motionPulse, 0.0f, deltaSeconds * 2.8f);

        String emotion = command == null ? "calm" : normalize(command.getEmotion());
        String motion = command == null ? "idle" : normalize(command.getMotion());
        float targetMouth = command == null ? 0.0f : clamp(command.getMouthOpen(), 0.0f, 1.0f);
        if (command == null || !command.getSpeaking()) {
            targetMouth *= 0.18f;
        }
        float lookX = command == null ? 0.0f : clamp(command.getLookX(), -1.0f, 1.0f);
        float lookY = command == null ? 0.0f : clamp(command.getLookY(), -1.0f, 1.0f);
        smoothedMouth = smooth(smoothedMouth, targetMouth, 0.45f);
        smoothedLookX = smooth(smoothedLookX, lookX, 0.18f);
        smoothedLookY = smooth(smoothedLookY, lookY, 0.18f);

        model.loadParameters();
        applyPoseParameters(emotion, motion);
        if (physics != null) {
            physics.evaluate(model, deltaSeconds);
        }
        model.saveParameters();
        model.update();
        isUpdated(true);
    }

    void draw(CubismMatrix44 projection) {
        CubismRendererAndroid renderer = this.<CubismRendererAndroid>getRenderer();
        if (model == null || renderer == null) {
            return;
        }
        CubismMatrix44 matrix = CubismMatrix44.create(projection);
        CubismMatrix44.multiply(modelMatrix.getArray(), matrix.getArray(), matrix.getArray());
        renderer.setMvpMatrix(matrix);
        renderer.drawModel();
    }

    private void setupTextures() {
        for (int i = 0; i < modelSetting.getTextureCount(); i++) {
            String textureFile = modelSetting.getTextureFileName(i);
            if (textureFile == null || textureFile.isEmpty()) {
                continue;
            }
            int textureId = createTexture(modelHomeDirectory + textureFile);
            ((CubismRendererAndroid) getRenderer()).bindTexture(i, textureId);
        }
        this.<CubismRendererAndroid>getRenderer().isPremultipliedAlpha(true);
    }

    private void normalizeMultiplyAndScreenColors() {
        if (model == null) {
            return;
        }
        CubismModelMultiplyAndScreenColor colors = model.getOverrideMultiplyAndScreenColor();
        colors.setMultiplyColorEnabled(true);
        colors.setScreenColorEnabled(true);
        for (int i = 0; i < model.getDrawableCount(); i++) {
            colors.setDrawableMultiplyColorEnabled(i, true);
            colors.setDrawableScreenColorEnabled(i, true);
            colors.setDrawableMultiplyColor(i, 1.0f, 1.0f, 1.0f, 1.0f);
            colors.setDrawableScreenColor(i, 0.0f, 0.0f, 0.0f, 1.0f);
        }
        for (int i = 0; i < model.getOffscreenCount(); i++) {
            colors.setOffscreenMultiplyColorEnabled(i, true);
            colors.setOffscreenScreenColorEnabled(i, true);
            colors.setOffscreenMultiplyColor(i, 1.0f, 1.0f, 1.0f, 1.0f);
            colors.setOffscreenScreenColor(i, 0.0f, 0.0f, 0.0f, 1.0f);
        }
    }

    private int createTexture(String assetPath) {
        Bitmap bitmap;
        try (InputStream input = appContext.getAssets().open(assetPath)) {
            BitmapFactory.Options options = new BitmapFactory.Options();
            options.inPremultiplied = true;
            bitmap = BitmapFactory.decodeStream(input, null, options);
        } catch (IOException e) {
            throw new IllegalStateException("Texture not found: " + assetPath, e);
        }
        if (bitmap == null) {
            throw new IllegalStateException("Texture decode failed: " + assetPath);
        }

        int[] textureId = new int[1];
        GLES20.glActiveTexture(GLES20.GL_TEXTURE0);
        GLES20.glGenTextures(1, textureId, 0);
        GLES20.glBindTexture(GLES20.GL_TEXTURE_2D, textureId[0]);
        GLUtils.texImage2D(GLES20.GL_TEXTURE_2D, 0, bitmap, 0);
        GLES20.glGenerateMipmap(GLES20.GL_TEXTURE_2D);
        GLES20.glTexParameteri(GLES20.GL_TEXTURE_2D, GLES20.GL_TEXTURE_MIN_FILTER, GLES20.GL_LINEAR_MIPMAP_LINEAR);
        GLES20.glTexParameteri(GLES20.GL_TEXTURE_2D, GLES20.GL_TEXTURE_MAG_FILTER, GLES20.GL_LINEAR);
        bitmap.recycle();
        return textureId[0];
    }

    private void indexParameters() {
        parameterIndices.clear();
        CubismModel cubismModel = getModel();
        for (int i = 0; i < cubismModel.getParameterCount(); i++) {
            String id = cubismModel.getParameterId(i).getString();
            parameterIndices.put(id, i);
        }
    }

    private void applyPoseParameters(String emotion, String motion) {
        float pulse = motionPulse * motionMultiplier(motion);
        float breath = (float) Math.sin(elapsedSeconds * 2.0f) * 0.5f + 0.5f;
        float blinkOpen = blinkOpenValue(emotion);
        float mouthForm = mouthFormFor(emotion);
        float browY = browYFor(emotion);
        float browForm = browFormFor(emotion);
        float angleX = smoothedLookX * 28.0f + pulse * 4.0f;
        float angleY = smoothedLookY * 18.0f - ("shy".equals(emotion) ? 4.0f : 0.0f);
        float angleZ = -smoothedLookX * 8.0f + pulse * 2.0f;

        setParam("ParamMouthOpenY", smoothedMouth);
        setParam("ParamMouthForm", mouthForm);
        setParam("ParamAngleX", angleX);
        setParam("ParamAngleY", angleY);
        setParam("ParamAngleZ", angleZ);
        setParam("ParamEyeLOpen", blinkOpen);
        setParam("ParamEyeROpen", blinkOpen);
        setParam("ParamEyeBallX", smoothedLookX);
        setParam("ParamEyeBallY", smoothedLookY);
        setParam("ParamBrowLY", browY);
        setParam("ParamBrowRY", browY);
        setParam("ParamBrowLForm", browForm);
        setParam("ParamBrowRForm", browForm);
        setParam("ParamBodyAngleX", smoothedLookX * 8.0f + pulse * 3.0f);
        setParam("ParamBodyAngleY", smoothedLookY * 5.0f);
        setParam("ParamBodyAngleZ", -smoothedLookX * 3.0f);
        setParam("ParamBreath", breath);
    }

    private void setParam(String id, float value) {
        Integer index = parameterIndices.get(id);
        if (index == null || model == null) {
            return;
        }
        model.setParameterValue(index, value);
    }

    private float blinkOpenValue(String emotion) {
        if ("sleep".equals(emotion)) {
            return 0.12f;
        }
        float phase = elapsedSeconds % 4.2f;
        if (phase < 0.08f) {
            return 1.0f - (phase / 0.08f);
        }
        if (phase < 0.16f) {
            return (phase - 0.08f) / 0.08f;
        }
        if ("happy".equals(emotion)) {
            return 0.82f;
        }
        return 1.0f;
    }

    private static String normalize(String value) {
        return value == null ? "" : value.toLowerCase(Locale.ROOT);
    }

    private static float mouthFormFor(String emotion) {
        if ("happy".equals(emotion)) return 0.65f;
        if ("shy".equals(emotion)) return 0.25f;
        if ("sad".equals(emotion)) return -0.45f;
        if ("angry".equals(emotion)) return -0.65f;
        if ("sleep".equals(emotion)) return -0.2f;
        return 0.0f;
    }

    private static float browYFor(String emotion) {
        if ("happy".equals(emotion)) return 0.35f;
        if ("shy".equals(emotion)) return -0.15f;
        if ("sad".equals(emotion)) return -0.35f;
        if ("angry".equals(emotion)) return -0.45f;
        if ("thinking".equals(emotion)) return 0.15f;
        return 0.0f;
    }

    private static float browFormFor(String emotion) {
        if ("happy".equals(emotion)) return 0.25f;
        if ("sad".equals(emotion)) return -0.4f;
        if ("angry".equals(emotion)) return -0.75f;
        if ("thinking".equals(emotion)) return -0.2f;
        return 0.0f;
    }

    private static float motionMultiplier(String motion) {
        if (motion.contains("taphead")) return 1.0f;
        if (motion.contains("taphand")) return 0.7f;
        if (motion.contains("tapbody")) return 0.6f;
        if (motion.contains("tapchest")) return 0.5f;
        if (motion.contains("stepback")) return -0.8f;
        return 0.25f;
    }

    private static float smooth(float current, float target, float ratio) {
        return current + (target - current) * clamp(ratio, 0.0f, 1.0f);
    }

    private static float approach(float current, float target, float delta) {
        if (current < target) {
            return Math.min(target, current + delta);
        }
        return Math.max(target, current - delta);
    }

    private static float safeDivide(float value, float divisor) {
        return divisor == 0.0f ? 0.0f : value / divisor;
    }

    private static float clamp(float value, float min, float max) {
        return Math.max(min, Math.min(max, value));
    }
}
