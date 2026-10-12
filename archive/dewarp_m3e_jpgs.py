import os
import sys
import cv2
import numpy as np
import piexif
from PIL import Image

def dewarp_image(in_path, out_path, camera_matrix, dist_coeffs):
    img = cv2.imread(in_path)
    if img is None:
        print(f"Failed to read {in_path}")
        return
    h, w = img.shape[:2]
    new_cam_mtx, _ = cv2.getOptimalNewCameraMatrix(camera_matrix, dist_coeffs, (w, h), 1, (w, h))
    undistorted = cv2.undistort(img, camera_matrix, dist_coeffs, None, new_cam_mtx)

    # Convert to RGB for PIL
    undistorted_rgb = cv2.cvtColor(undistorted, cv2.COLOR_BGR2RGB)
    pil_img = Image.fromarray(undistorted_rgb)

    # Try to preserve EXIF
    try:
        exif_dict = piexif.load(in_path)
        exif_bytes = piexif.dump(exif_dict)
        pil_img.save(out_path, "JPEG", exif=exif_bytes)
    except Exception as e:
        print(f"Warning: EXIF not preserved for {in_path}: {e}")
        pil_img.save(out_path, "JPEG")

def main():
    if len(sys.argv) < 2:
        print("Usage: python dewarp_m3e.py <folder_path>")
        sys.exit(1)

    folder = sys.argv[1]
    if not os.path.isdir(folder):
        print(f"Error: {folder} is not a directory.")
        sys.exit(1)

    out_dir = os.path.join(folder, "Dewarped_Images")
    os.makedirs(out_dir, exist_ok=True)

    # DJI M3E calibrated intrinsics & distortion
    camera_matrix = np.array([
        [3713.29,    0.0,  2640.0],
        [0.0,     3713.29, 1978.0],
        [0.0,       0.0,     1.0]
    ], dtype=np.float32)
    dist_coeffs = np.array([
        -0.11257524,  # k1
         0.01487443,  # k2
        -0.00008572,  # p1
         0.00000010,  # p2
        -0.02706411   # k3
    ], dtype=np.float32)

    for fname in os.listdir(folder):
        if fname.lower().endswith((".jpg", ".jpeg")):
            src = os.path.join(folder, fname)
            dst = os.path.join(out_dir, fname)
            if os.path.exists(dst):
                print(f"Skipping (exists): {fname}")
                continue
            print(f"Processing: {fname}")
            dewarp_image(src, dst, camera_matrix, dist_coeffs)

    print("All done! Dewarped images in:", out_dir)

if __name__ == "__main__":
    main()
