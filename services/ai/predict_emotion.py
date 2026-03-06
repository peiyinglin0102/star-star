import cv2
import numpy as np
from fer import FER
import mediapipe as mp
from PIL import Image, ImageEnhance, ImageSequence  # ← 新增 ImageSequence
import random
import os
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

ALL_EMOTIONS = ["angry", "disgust", "fear", "happy", "sad", "surprise"]

# 建立 FER 模型
emotion_detector = FER(mtcnn=False)

# --- 全域重用的 MediaPipe 偵測器（效能更穩） ---
mp_face_detection = mp.solutions.face_detection
_FACE_DET = mp_face_detection.FaceDetection(model_selection=1, min_detection_confidence=0.3)

def enhance_emotion_features(image):
    """強化面部情緒特徵（保留你的版本）"""
    try:
        pil_image = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
        pil_image = ImageEnhance.Contrast(pil_image).enhance(1.4)
        pil_image = ImageEnhance.Sharpness(pil_image).enhance(1.3)
        enhanced = cv2.cvtColor(np.array(pil_image), cv2.COLOR_RGB2BGR)

        lab = cv2.cvtColor(enhanced, cv2.COLOR_BGR2LAB)
        l, a, b = cv2.split(lab)
        clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8,8))
        l = clahe.apply(l)
        lab = cv2.merge([l, a, b])
        return cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)
    except:
        return image

def _detect_face_once(image_bgr):
    """用全域的 MediaPipe 偵測一次臉，回傳擴大後的臉部區域（或 None）。"""
    rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
    result = _FACE_DET.process(rgb)
    if not result.detections:
        return None, "❌ 未偵測到人臉"

    detection = max(result.detections, key=lambda d:
                    d.location_data.relative_bounding_box.width *
                    d.location_data.relative_bounding_box.height)
    bbox = detection.location_data.relative_bounding_box
    h, w, _ = image_bgr.shape

    x = max(int(bbox.xmin * w) - int(bbox.width * w * 0.4), 0)
    y = max(int(bbox.ymin * h) - int(bbox.height * h * 0.5), 0)
    x2 = min(int((bbox.xmin + bbox.width) * w) + int(bbox.width * w * 0.4), w)
    y2 = min(int((bbox.ymin + bbox.height) * h) + int(bbox.height * h * 0.5), h)

    face_img = image_bgr[y:y2, x:x2]
    if face_img.shape[0] < 80 or face_img.shape[1] < 80:
        return None, "❌ 偵測到的人臉太小"

    face_img = cv2.resize(face_img, (300, 300))
    return enhance_emotion_features(face_img), None

def _iter_candidate_frames(path, max_frames=12):
    """
    盡量兼容靜態圖、GIF/WEBP 動圖。
    - GIF/WEBP：從多幀裡等距抽樣（最多 max_frames 幀）
    - 其它：就回傳單一幀
    皆回傳 BGR 影像（numpy array）
    """
    try:
        with Image.open(path) as im:
            frame_count = getattr(im, "n_frames", 1)
            if frame_count <= 1:
                # 靜態圖
                bgr = cv2.cvtColor(np.array(im.convert("RGB")), cv2.COLOR_RGB2BGR)
                yield bgr
            else:
                # 動圖：等距抽樣
                step = max(1, frame_count // max_frames)
                picks = list(range(0, frame_count, step))[:max_frames]
                for idx in picks:
                    try:
                        im.seek(idx)
                        rgb = im.convert("RGB")
                        bgr = cv2.cvtColor(np.array(rgb), cv2.COLOR_RGB2BGR)
                        yield bgr
                    except Exception as e:
                        logger.warning(f"讀取第 {idx} 幀失敗：{e}")
                        continue
    except Exception:
        # PIL 失敗就退回 OpenCV（多半只能讀靜態）
        bgr = cv2.imread(path)
        if bgr is not None:
            yield bgr

def analyze_emotion_with_fer(face_img):
    """使用 FER 模型分析情緒（保留你的版本）"""
    try:
        rgb = cv2.cvtColor(face_img, cv2.COLOR_BGR2RGB)
        results = emotion_detector.detect_emotions(rgb)
        if not results:
            return None
        emotions = results[0]["emotions"]
        dominant = max(emotions, key=emotions.get)
        return {
            "dominant_emotion": dominant,
            "average_scores": {k: round(v * 100, 2) for k, v in emotions.items()}
        }
    except Exception as e:
        logger.error(f"FER 分析錯誤: {str(e)}")
        return None

def apply_emotion_correction_rules(analysis):
    """你的規則：fear/sad 調整、happy 低分修正等（保留原樣）"""
    scores = analysis["average_scores"]
    dominant = analysis["dominant_emotion"]

    sorted_scores = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    top3 = dict(sorted_scores[:3])

    if "fear" in scores and "sad" in scores:
        if abs(scores["sad"] - scores["fear"]) < 10 and scores["sad"] > 25:
            if dominant == "fear":
                logger.info(f"修正：fear → sad (分數接近)")
                dominant = "sad"

    if scores.get("sad", 0) > 30 and "sad" in top3:
        dominant = "sad"

    if scores.get("angry", 0) > 25 and scores.get("sad", 0) > 25:
        if scores["sad"] >= scores["angry"]:
            dominant = "sad"

    if dominant == "happy" and scores.get("happy", 0) < 40:
        for alt in ["sad", "fear", "surprise"]:
            if scores.get(alt, 0) > scores["happy"] * 0.8:
                logger.info(f"修正：低分 happy → {alt}")
                dominant = alt
                break

    return {
        "corrected_emotion": dominant,
        "corrected_scores": scores,
        "original_dominant": analysis["dominant_emotion"]
    }

def predict_emotion(image_path):
    """
    主函數：支援靜態圖 + GIF/WEBP 動圖
    - 依序嘗試多幀，取到「有臉且 FER 分析成功」的最佳結果
    """
    try:
        if not os.path.exists(image_path):
            return {"error": "❌ 圖片檔案不存在"}

        best = None  # (confidence, analysis, final_emotion, scores)
        last_face_err = None

        for frame_bgr in _iter_candidate_frames(image_path, max_frames=12):
            if frame_bgr is None:
                continue

            face_img, face_err = _detect_face_once(frame_bgr)
            if face_err:
                last_face_err = face_err
                continue

            analysis = analyze_emotion_with_fer(face_img)
            if not analysis:
                continue

            result = apply_emotion_correction_rules(analysis)
            final_emotion = result["corrected_emotion"]
            scores = result["corrected_scores"]
            confidence = (scores.get(final_emotion, 0) / 100.0)

            # 設一個比較直覺的「最佳」挑選：信心值越高越好
            if (best is None) or (confidence > best[0]):
                best = (confidence, analysis, final_emotion, scores, result)

            # 如果已經很高了就早停（可調整閾值）
            if confidence >= 0.85:
                break

        if best is None:
            # 都沒有成功的人臉或分析
            return {"error": last_face_err or "❌ 無法分析情緒"}

        confidence, analysis, final_emotion, scores, result = best

        if confidence < 0.35:
            return {"error": f"❌ 信心值過低（{round(confidence, 2)}），建議重新拍攝或人工標註"}

        if final_emotion not in ALL_EMOTIONS:
            valid = {k: v for k, v in scores.items() if k in ALL_EMOTIONS}
            if valid:
                final_emotion = max(valid, key=valid.get)
                confidence = valid[final_emotion] / 100.0
            else:
                return {"error": "❌ 無有效情緒"}

        wrong_option = random.choice([e for e in ALL_EMOTIONS if e != final_emotion])
        level = "很高" if confidence >= 0.75 else "高" if confidence >= 0.6 else "中" if confidence >= 0.45 else "低"

        return {
            "label": final_emotion,
            "confidence": round(confidence, 3),
            "confidence_level": level,
            "wrong_option": wrong_option,
            "all_emotion_scores": {k: round(v/100, 3) for k, v in scores.items() if k in ALL_EMOTIONS},
            "debug_info": f"原始: {result['original_dominant']}, 修正後: {final_emotion}",
            "analysis_details": {
                "vote_count": 1,
                "was_corrected": final_emotion != result["original_dominant"]
            }
        }

    except Exception as e:
        logger.error(f"系統錯誤: {e}")
        return {"error": f"❌ 系統錯誤：{str(e)}"}
