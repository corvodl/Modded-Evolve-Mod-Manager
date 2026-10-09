"""Convert CryXmlB files in an extracted ZIP to readable XML. Standard library only."""
import struct,xml.etree.ElementTree as ET,zipfile,argparse,json
from pathlib import Path

def decode(b):
 if not b.startswith(b'CryXmlB\0'):raise ValueError('Not CryXmlB')
 size,no,nn,ao,an,co,cn,so,sn=struct.unpack_from('<9I',b,8)
 if size!=len(b):raise ValueError('File size mismatch')
 for o,n,s in [(no,nn,28),(ao,an,8),(co,cn,4),(so,sn,1)]:
  if o+n*s>len(b):raise ValueError('Table outside file')
 def string(i):
  if i>=sn:raise ValueError('String offset outside table')
  end=b.index(b'\0',so+i,so+sn);return b[so+i:end].decode('utf8')
 nodes=[]; records=[]
 for i in range(nn):
  tag,content,na,nc,parent,fa,fc,reserved=struct.unpack_from('<IIHHIIII',b,no+i*28)
  if fa+na>an or fc+nc>cn:raise ValueError('Invalid node range')
  e=ET.Element(string(tag));e.text=string(content) or None
  for j in range(fa,fa+na):
   k,v=struct.unpack_from('<II',b,ao+8*j);key=string(k)
   if key in e.attrib:raise ValueError('Duplicate attribute')
   e.set(key,string(v))
  nodes.append(e);records.append((parent,fc,nc))
 seen=set()
 def attach(i):
  if i in seen:raise ValueError('Cycle or multiple parents')
  seen.add(i);parent,fc,nc=records[i]
  for j in range(fc,fc+nc):
   child=struct.unpack_from('<I',b,co+4*j)[0]
   if child>=nn or records[child][0]!=i:raise ValueError('Parent/child mismatch')
   nodes[i].append(attach(child))
  return nodes[i]
 roots=[i for i,r in enumerate(records) if r[0]==0xffffffff]
 if len(roots)!=1:raise ValueError('Expected one root')
 root=attach(roots[0])
 if len(seen)!=nn:raise ValueError('Unreachable nodes')
 return root

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('archive');p.add_argument('-o',default='readable_libs');a=p.parse_args();out=Path(a.o);report=[]
 with zipfile.ZipFile(a.archive) as z:
  for entry in z.infolist():
   if entry.is_dir():continue
   name=entry.filename.replace('\\','/');rel=Path(name)
   if rel.is_absolute() or '..' in rel.parts:raise ValueError('Unsafe path')
   b=z.read(entry);dest=out/rel;dest.parent.mkdir(parents=True,exist_ok=True)
   try:
    if b.startswith(b'CryXmlB\0'):
     root=decode(b);ET.indent(root,space='  ');converted=ET.tostring(root,encoding='utf-8',xml_declaration=True)
     ET.fromstring(converted);dest.write_bytes(converted);status='decoded'
    else:dest.write_bytes(b);status='copied'
    report.append({'file':name,'status':status})
   except Exception as e:report.append({'file':name,'status':'error','error':str(e)})
 out.mkdir(parents=True,exist_ok=True);(out/'conversion_report.json').write_text(json.dumps(report,indent=2))
 from collections import Counter
 print(dict(Counter(x['status'] for x in report)))
 print([x for x in report if x['status']=='error'][:5])
if __name__=='__main__':main()
