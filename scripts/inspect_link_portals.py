#!/usr/bin/env python3
"""
Script demonstrating pdfatlas library usage to:
1. Open a PDF document using DocumentModel.
2. Retrieve links from a specific page (e.g. Page 8).
3. Resolve destination link coordinates correctly across link types.
4. Render portal previews programmatically to PNG.
"""

import sys
from pathlib import Path
from pdfatlas.core.document import DocumentModel

def main():
    pdf_path = Path("sandbox.local/sample-files/deepseek-cordis.local.pdf")

    if not pdf_path.exists():
        print(f"Error: {pdf_path} not found.")
        sys.exit(1)

    print(f"Opening document: {pdf_path}")
    doc_model = DocumentModel(str(pdf_path))
    print(f"Total pages: {doc_model.page_count}")

    # Inspect Page 8 (0-indexed page 7)
    page_idx = 7
    page = doc_model.get_page(page_idx)
    links = doc_model.get_page_links(page_idx)

    print(f"\n--- Links on Page {page_idx + 1} ({len(links)} total) ---")

    output_dir = Path("sandbox.local/portal-samples")
    output_dir.mkdir(parents=True, exist_ok=True)


    target_refs = ["19", "33"]

    for i, link in enumerate(links):
        from_rect = link.get("from")
        words = " ".join(w[4] for w in page.get_text("words", clip=from_rect))
        target_page = link.get("page")
        
        if target_page is None:
            continue

        target_y = doc_model.resolve_link_target_y(link)
        kind = link.get("kind")
        to_point = link.get("to")
        
        print(f"\nLink {i}: Ref text={repr(words)}")
        print(f"  Type: Kind {kind}")
        print(f"  Target Page: {target_page + 1} (index {target_page})")
        print(f"  Raw `to` point: {to_point}")
        print(f"  Resolved Top-Down Target Y: {target_y:.2f} pt")

        # Save portal image for target refs (19, 33) or others
        for ref_num in target_refs:
            if ref_num in words or words == ref_num:
                pix = doc_model.render_portal_pixmap(
                    page_index=target_page,
                    target_y=target_y,
                    target_w=600,
                    target_h=200,
                )
                output_path = output_dir / f"ref_{ref_num}_portal.png"
                pix.save(str(output_path))
                print(f"  -> Saved portal preview to: {output_path}")

    doc_model.close()
    print("\nDone!")

if __name__ == "__main__":
    main()
