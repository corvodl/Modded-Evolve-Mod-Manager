import sys,json,tempfile,time
from pathlib import Path
from unittest.mock import patch
import asset_library as lib
import asset_library_preview as preview
from build_collections import register_build,assign_archive,build_index
from level_asset_mount import mount_map_index

with tempfile.TemporaryDirectory() as tmp:
 root=Path(tmp); library=root/'library'; workspace=root/'project'
 (workspace/'files').mkdir(parents=True)
 ids=['a'*20,'b'*20]
 for ident,count in zip(ids,[1200,2400]):
  folder=library/ident; (folder/'files/objects').mkdir(parents=True)
  entries=[]
  for n in range(count):
   virtual=f'objects/item{n}.cgf'; (folder/'files'/virtual).write_bytes(b'test')
   entries.append(dict(path=virtual,bytes=4,sha256='unused'))
  (folder/lib.MANIFEST).write_text(json.dumps(dict(version=1,name=ident+'.pak',source=ident+'.pak',source_sha256=ident,extracted_bytes=count*4,entries=entries)))
 build=register_build('selected','early',root=library)
 assign_archive(build,ids[0],root=library)
 rows=[]
 def measure(name,fn):
  with patch('asset_library._load_manifest',wraps=lib._load_manifest) as a, patch('asset_library_preview._load_manifest',wraps=preview._load_manifest) as b:
   start=time.perf_counter(); result=fn(); elapsed=time.perf_counter()-start
   rows.append(dict(operation=name,seconds=round(elapsed,4),manifest_reads=a.call_count+b.call_count))
 lib._invalidate()
 measure('model_index_cold',lambda:preview.library_model_index(ids[0],library,build_id=build))
 measure('model_index_warm',lambda:preview.library_model_index(ids[0],library,build_id=build))
 measure('export_one_file',lambda:preview.export_file(ids[0],'objects/item0.cgf',root/'one.cgf',root=library))
 with patch('evolve_pak_workspace.load_workspace',return_value={'entries':[]}):
  measure('map_mount',lambda:mount_map_index(workspace,'tutorial',library_root=library,build_id=build))
 print(json.dumps(rows,indent=2),flush=True)
 if len(sys.argv)>1:Path(sys.argv[1]).write_text(json.dumps(rows,indent=2))
