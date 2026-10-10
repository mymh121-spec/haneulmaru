"""Platform adapters. Native S22 capture can implement FrameSource later."""
import time
from .contracts import Frame, SetupError


class OpenCVSource:
    def __init__(self, source, rotation=0):
        import cv2
        if rotation not in (0, 90, 180, 270):
            raise ValueError("rotation: 0/90/180/270 clockwise")
        if not source:
            raise ValueError("S22 영상 스트림 주소 또는 녹화 파일을 지정하세요")
        self.cv2, self.rotation = cv2, rotation
        self.sequence = 0
        self.cap = cv2.VideoCapture(int(source) if str(source).isdecimal() else str(source))
        if not self.cap.isOpened():
            self.cap.release()
            raise SetupError("S22 영상 입력을 열 수 없습니다. 주소 또는 파일을 확인하세요")
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    def read(self):
        ok, image = self.cap.read()
        if not ok:
            return None
        self.sequence += 1
        captured = time.monotonic() * 1000
        rotations = {90: self.cv2.ROTATE_90_CLOCKWISE, 180: self.cv2.ROTATE_180,
                     270: self.cv2.ROTATE_90_COUNTERCLOCKWISE}
        if self.rotation:
            image = self.cv2.rotate(image, rotations[self.rotation])
        return Frame(self.sequence, captured, image)

    def close(self):
        self.cap.release()


class NoImu:
    def start(self):
        pass

    def stop(self):
        pass

    def snapshot(self):
        return {"sample": None, "age_ms": None, "source": None, "sources": {}}
