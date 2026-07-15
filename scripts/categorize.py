#!/usr/bin/env python3
import os
import sys
import time
import fitz
import numpy as np

def main():
    if len(sys.argv) < 2:
        print("Usage: python categorize.py <path_to_pdf>")
        sys.exit(1)

    pdf_path = sys.argv[1]
    if not os.path.exists(pdf_path):
        print(f"Error: file not found: {pdf_path}")
        sys.exit(1)

    try:
        doc = fitz.open(pdf_path)
    except Exception as e:
        print(f"Error: failed to open PDF: {e}")
        sys.exit(1)

    pdf_basename = os.path.basename(pdf_path)
    print(f"Layout Categorization & Benchmark for '{pdf_basename}':\n")
    
    # 10 DPI zoom factor (10 / 72)
    zoom = 10.0 / 72.0
    matrix = fitz.Matrix(zoom, zoom)

    page_times_old = []
    page_times_new_a = []
    page_times_new_b = []
    rows_data = []

    for i in range(len(doc)):
        page = doc[i]
        
        # --- 1. Old Heuristic (Pixel-based) ---
        t0 = time.perf_counter()
        pix = page.get_pixmap(matrix=matrix)
        arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape((pix.height, pix.width, pix.n))
        gray = np.mean(arr[:, :, 0:3], axis=2) if pix.n >= 3 else arr[:, :, 0]
        binary = (gray >= 240)
        h, w = binary.shape
        if w < 5:
            old_res = "N/A"
            old_time = (time.perf_counter() - t0) * 1000.0
        else:
            top_crop = int(h * 0.1)
            bottom_crop = int(h * 0.9)
            cropped = binary[top_crop:bottom_crop, :]
            mid_start = int(w * 0.45)
            mid_end = max(mid_start + 1, int(w * 0.55))
            has_gutter = False
            for col_idx in range(mid_start, mid_end):
                if cropped[:, col_idx].all():
                    has_gutter = True
                    break
            old_res = "Double-column" if has_gutter else "Single-column"
            old_time = (time.perf_counter() - t0) * 1000.0
        page_times_old.append(old_time)
        
        # --- 2. Approach A (Y-Banding & Text Block Bboxes) ---
        t0 = time.perf_counter()
        blocks = [b for b in page.get_text("blocks") if b[6] == 0]
        width = page.rect.width
        height = page.rect.height
        mid = width / 2.0
        num_slices = 50
        slice_h = height / num_slices
        two_col_slices = 0
        single_col_slices = 0
        for s in range(num_slices):
            sy0 = s * slice_h
            sy1 = (s + 1) * slice_h
            slice_blocks = []
            for b in blocks:
                bx0, by0, bx1, by1 = b[0:4]
                if by0 < sy1 and by1 > sy0:
                    slice_blocks.append(b)
            if not slice_blocks:
                continue
            has_left = any(b[2] <= mid + 5 for b in slice_blocks)
            has_right = any(b[0] >= mid - 5 for b in slice_blocks)
            has_spanning = any(b[0] < mid - 20 and b[2] > mid + 20 for b in slice_blocks)
            if has_left and has_right and not has_spanning:
                two_col_slices += 1
            else:
                single_col_slices += 1
        total_slices = two_col_slices + single_col_slices
        new_a_fraction = two_col_slices / total_slices if total_slices > 0 else 0
        new_a_res = "Double-column" if new_a_fraction >= 0.3 else "Single-column"
        new_a_time = (time.perf_counter() - t0) * 1000.0
        page_times_new_a.append(new_a_time)
        
        # --- 3. Approach B (Segmented Projection Profiles) ---
        t0 = time.perf_counter()
        pix_b = page.get_pixmap(matrix=matrix)
        arr_b = np.frombuffer(pix_b.samples, dtype=np.uint8).reshape((pix_b.height, pix_b.width, pix_b.n))
        gray_b = np.mean(arr_b[:, :, 0:3], axis=2) if pix_b.n >= 3 else arr_b[:, :, 0]
        binary_b = (gray_b >= 240)
        h_b, w_b = binary_b.shape
        if w_b < 5:
            new_b_stats = "N/A"
            new_b_time = (time.perf_counter() - t0) * 1000.0
        else:
            top_crop = int(h_b * 0.1)
            bottom_crop = int(h_b * 0.9)
            cropped_b = binary_b[top_crop:bottom_crop, :]
            seg_h = cropped_b.shape[0] // 4
            mid_start_b = int(w_b * 0.45)
            mid_end_b = max(mid_start_b + 1, int(w_b * 0.55))
            
            gutters_found = 0
            for s in range(4):
                s_start = s * seg_h
                s_end = (s + 1) * seg_h if s < 3 else cropped_b.shape[0]
                seg_cropped = cropped_b[s_start:s_end, :]
                seg_has_gutter = False
                for x in range(mid_start_b, mid_end_b):
                    if seg_cropped[:, x].all():
                        seg_has_gutter = True
                        break
                if seg_has_gutter:
                    gutters_found += 1
            new_b_stats = f"gutter in {gutters_found}/4 segs"
            new_b_time = (time.perf_counter() - t0) * 1000.0
        page_times_new_b.append(new_b_time)
        
        # Append row data
        rows_data.append((i + 1, old_res, old_time, new_a_res, new_a_time, new_b_stats, new_b_time))

    # Unicode Box-Drawing Border & Grid Definitions (aligned to maximum lengths)
    top_border  = "┌" + "─"*4 + "┬" + "─"*29 + "┬" + "─"*29 + "┬" + "─"*34 + "┐"
    mid_border  = "├" + "─"*4 + "┼" + "─"*15 + "┬" + "─"*13 + "┼" + "─"*15 + "┬" + "─"*13 + "┼" + "─"*20 + "┬" + "─"*13 + "┤"
    row_border  = "├" + "─"*4 + "┼" + "─"*15 + "┼" + "─"*13 + "┼" + "─"*15 + "┼" + "─"*13 + "┼" + "─"*20 + "┼" + "─"*13 + "┤"
    bot_border  = "└" + "─"*4 + "┴" + "─"*15 + "┴" + "─"*13 + "┴" + "─"*15 + "┴" + "─"*13 + "┴" + "─"*20 + "┴" + "─"*13 + "┘"
    
    header1     = "│Page│    Old Heuristic (Pixel)    │   Approach A (Y-Banding)    │      Approach B (Segmented)      │"
    header2     = "│    │    Result     │  Time (ms)  │    Result     │  Time (ms)  │    Stats Notes     │  Time (ms)  │"
    row_format  = "│{:^4}│ {:^13} │ {:>8.2f} ms │ {:^13} │ {:>8.2f} ms │ {:^18} │ {:>8.2f} ms │"

    print(top_border)
    print(header1)
    print(mid_border)
    print(header2)
    print(row_border)
    for idx, row in enumerate(rows_data):
        print(row_format.format(*row))
        if idx < len(rows_data) - 1:
            print(row_border)
    print(bot_border)

    # Calculate statistics
    def get_stats(times):
        if not times:
            return 0.0, 0.0, 0.0
        return min(times), max(times), sum(times) / len(times)
        
    old_min, old_max, old_avg = get_stats(page_times_old)
    new_a_min, new_a_max, new_a_avg = get_stats(page_times_new_a)
    new_b_min, new_b_max, new_b_avg = get_stats(page_times_new_b)
    
    print("\n--- Summary Statistics (Min / Max / Avg) ---")
    print(f"Old Heuristic (Pixel):    min = {old_min:.2f} ms, max = {old_max:.2f} ms, avg = {old_avg:.2f} ms")
    print(f"Approach A (Y-Banding):   min = {new_a_min:.2f} ms, max = {new_a_max:.2f} ms, avg = {new_a_avg:.2f} ms")
    print(f"Approach B (Segmented):   min = {new_b_min:.2f} ms, max = {new_b_max:.2f} ms, avg = {new_b_avg:.2f} ms")

if __name__ == '__main__':
    main()
