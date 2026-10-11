"""Material properties and referenced-texture editing for extracted .mtl files.

Surgical XML span replacement: never reserialize CryEngine material trees.
"""
from __future__ import annotations
from dataclasses import dataclass
import re
from xml.parsers import expat

from studio_tools import numeric_fields, apply_numeric_edits
from workspace_editor import validate_xml

TEXTURE_TAG=re.compile(r'<Texture\b[^>]*?/?>',re.I|re.S)
FILE_ATTR=re.compile(r'\bFile\s*=\s*(["\x27])([^"\x27]*)\1',re.I|re.S)
MAX_MTL=2*1024*1024


@dataclass(frozen=True)
class TextureReference:
    index:int
    offset:int
    end:int
    value:str


def reference_fields(xml):
    if len(xml.encode('utf-8'))>MAX_MTL:
        raise ValueError('Material exceeds visual editing limit')
    validate_xml(xml.encode())
    matches=[]
    for tag in TEXTURE_TAG.finditer(xml):
        for field in FILE_ATTR.finditer(tag.group()):
            start=tag.start()+field.start(2)
            end=tag.start()+field.end(2)
            matches.append(TextureReference(len(matches),start,end,field.group(2)))
    return tuple(matches)


def material_adjust(xml,attributes=None,textures=None):
    attributes=attributes or {}
    textures=textures or {}
    refs=reference_fields(xml)
    edited=apply_numeric_edits(xml,{int(k):str(v) for k,v in attributes.items()})
    if not textures:
        return edited
    # Apply texture replacements on the numeric-adjusted text; offsets may
    # shift when an earlier numeric property was modified.
    refs=reference_fields(edited)
    changes=[]
    for index,value in textures.items():
        if not isinstance(index,int) or not 0<=index<len(refs):
            raise ValueError('Unknown texture slot.')
        ref=refs[index]
        if (not isinstance(value,str) or len(value)>240 or
                any(ch in value for ch in ('\x00','"',"'","<",">",'&')) or
                not value.casefold().endswith(('.dds','.dds.0','.tif','.tga'))):
            raise ValueError('Unsupported CryEngine texture reference.')
        if value.startswith(('/', '\\')) or ':' in value or '..' in value.replace('\\','/').split('/'):
            raise ValueError('Texture reference must stay relative.')
        changes.append((ref.offset,ref.end,value.replace('\\','/')))
    for start,end,value in sorted(changes,reverse=True):
        edited=edited[:start]+value+edited[end:]
    validate_xml(edited.encode(),xml.encode())
    return edited
