import os
import sys
import cv2
import numpy as np
import piexif
from PIL import Image

"""
dewarp_m3e_cropped.py

Usage:
    Drag-and-drop a folder of DJI M3E JPGs onto this script
    (or run: python dewarp_m3e_cropped.py "path/to/your/folder")

It will:
 - Undistort (dewarp) each image using DJI’s calibrated intrinsics
 - Crop out the black borders
 - Preserve any transferable EXIF metadata
 - Save results in <your_folder>/Dewarped_Images_Cropped
"""

def dewarp_and_crop(src_path, dst_path, camera_matrix, dist_coeffs):
    img = cv2.imread(src_path)
    if img is None:
        print(f"Failed to read {src_path}")
        return

    h, w = img.shape[:2]
    # alpha=0 crops out all black border areas
    new_cam_mtx, roi = cv2.getOptimalNewCameraMatrix(
        camera_matrix, dist_coeffs, (w, h), 0, (w, h)
    )
    undistorted = cv2.undistort(img, camera_matrix, dist_coeffs, None, new_cam_mtx)

    # Crop to valid region
    x, y, w_roi, h_roi = roi
    cropped = undistorted[y:y+h_roi, x:x+w_roi]

    # Convert to RGB for PIL
    cropped_rgb = cv2.cvtColor(cropped, cv2.COLOR_BGR2RGB)
    pil_img = Image.fromarray(cropped_rgb)

    # Try to preserve EXIF
    try:
        exif_dict = piexif.load(src_path)
        exif_bytes = piexif.dump(exif_dict)
        pil_img.save(dst_path, "JPEG", exif=exif_bytes)
    except Exception as e:
        print(f"Warning: EXIF not preserved for {os.path.basename(src_path)}: {e}")
        pil_img.save(dst_path, "JPEG")

def main():
    if len(sys.argv) < 2:
        print("Usage: python dewarp_m3e_cropped.py <folder_path>")
        sys.exit(1)

    folder = sys.argv[1]
    if not os.path.isdir(folder):
        print(f"Error: {folder} is not a directory.")
        sys.exit(1)

    # Make output subfolder inside the input folder
    out_folder = os.path.join(folder, "Dewarped_Images_Cropped")
    os.makedirs(out_folder, exist_ok=True)

    # DJI M3E calibrated parameters
    camera_matrix = np.array([
        [3713.29,    0.0,   2640.0],
        [0.0,     3713.29,  1978.0],
        [0.0,        0.0,      1.0]
    ], dtype=np.float32)
    dist_coeffs = np.array([
        -0.11257524,  # k1
         0.01487443,  # k2
        -0.00008572,  # p1
         0.00000010,  # p2
        -0.02706411   # k3
    ], dtype=np.float32)

    for fname in os.listdir(folder):
        if not fname.lower().endswith((".jpg", ".jpeg")):
            continue
        src = os.path.join(folder, fname)
        dst = os.path.join(out_folder, fname)
        if os.path.exists(dst):
            print(f"Skipping (already exists): {fname}")
            continue
        print(f"Processing: {fname}")
        dewarp_and_crop(src, dst, camera_matrix, dist_coeffs)

    print("\nAll done! De-warped & cropped images here:\n", out_folder)

if __name__ == "__main__":
    main()
