import zipfile, re
z = zipfile.ZipFile(r'd:\Wang\下载D\duan_LU\duan_LU\645协议低压塑壳断路器通信规约-带漏电保护(3)(1).docx')
x = z.read('word/document.xml').decode('utf-8', errors='replace')
# split paragraphs
paras = re.split(r'</w:p>', x)
out = []
for p in paras:
    txt = ''.join(re.findall(r'<w:t[^>]*>([^<]*)</w:t>', p))
    if txt.strip():
        out.append(txt.strip())
text = '\n'.join(out)
open(r'd:\Wang\下载D\duan_LU\doc_paras.txt', 'w', encoding='utf-8').write(text)
print('lines:', len(out))