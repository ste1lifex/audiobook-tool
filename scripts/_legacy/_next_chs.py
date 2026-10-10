import os
d = r"epub_extract\simplified"
for f in ["book1_part0008.txt", "book1_part0009.txt", "book1_part0010.txt", "book1_part0011.txt", "book1_part0012.txt"]:
    with open(os.path.join(d, f), encoding="utf-8") as fh:
        n = len(fh.read().replace(chr(10), ""))
    print(f"{f}: {n} 字")
