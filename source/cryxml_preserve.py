"""Conservative value edits that retain original CryXmlB tables and offsets."""
import struct

def _in_place(original, edited):
    from evolve_gameplay_editor import decode_cryxml, semantic, strip_pretty_indentation
    strip_pretty_indentation(edited)
    prior = decode_cryxml(original)
    size,no,nn,ao,an,co,cn,so,sn = struct.unpack_from('<9I',original,8)
    desired = {}
    def request(offset, value):
        end=original.index(b'\0',so+offset,so+sn)
        old=original[so+offset:end]
        new=value.encode('utf-8')
        if b'\0' in new:raise ValueError('NUL characters are not supported.')
        if len(new)!=len(old):
            raise ValueError('Safe XML editing requires the same UTF-8 byte length: '
                             f'{old.decode("utf-8")!r} -> {value!r}. '
                             'Use the same number of digits; variable-length edits are not yet supported.')
        if offset in desired and desired[offset]!=new:
            raise ValueError('This value shares binary storage with another field. '
                             'Changing just one reference is not supported in safe mode.')
        desired[offset]=new
    def visit(index, before, after):
        if before.tag!=after.tag or list(before.attrib)!=list(after.attrib) or len(before)!=len(after):
            raise ValueError('Safe XML editing supports existing values only. Do not add, remove, reorder, or rename tags/attributes.')
        tag,content,na,nc,parent,fa,fc,_=struct.unpack_from('<IIHHIIII',original,no+index*28)
        request(tag,after.tag);request(content,after.text or '')
        for j,(key,value) in enumerate(after.attrib.items()):
            ko,vo=struct.unpack_from('<II',original,ao+(fa+j)*8)
            request(ko,key);request(vo,value)
        for j,(left,right) in enumerate(zip(before,after)):
            child=struct.unpack_from('<I',original,co+(fc+j)*4)[0]
            visit(child,left,right)
    roots=[i for i in range(nn) if struct.unpack_from('<I',original,no+i*28+12)[0]==0xffffffff]
    visit(roots[0],prior,edited)
    out=bytearray(original)
    for offset,value in desired.items():out[so+offset:so+offset+len(value)]=value
    result=bytes(out)
    if semantic(decode_cryxml(result))!=semantic(edited):
        raise ValueError('Shared or overlapping binary strings prevent this edit. Original data preserved.')
    assert len(result)==len(original) and result[:so]==original[:so]
    return result


def preserve_values(original, edited):
    """Prefer proven in-place edits; isolate shared/new strings when necessary."""
    from evolve_gameplay_editor import decode_cryxml, semantic, strip_pretty_indentation
    strip_pretty_indentation(edited)
    prior=decode_cryxml(original)
    size,no,nn,ao,an,co,cn,so,sn=struct.unpack_from('<9I',original,8)
    # Validate topology before attempting either encoding route.
    changes=[]
    def visit(index,before,after):
        if before.tag!=after.tag or list(before.attrib)!=list(after.attrib) or len(before)!=len(after):
            raise ValueError('Safe XML editing supports existing values only. Do not add, remove, reorder, or rename tags/attributes.')
        tag,content,na,nc,parent,fa,fc,_=struct.unpack_from('<IIHHIIII',original,no+index*28)
        if (before.text or '')!=(after.text or ''):
            changes.append((no+index*28+4,after.text or ''))
        for j,(key,value) in enumerate(after.attrib.items()):
            if before.attrib[key]!=value:changes.append((ao+(fa+j)*8+4,value))
        for j,(left,right) in enumerate(zip(before,after)):
            child=struct.unpack_from('<I',original,co+(fc+j)*4)[0]
            visit(child,left,right)
    roots=[i for i in range(nn) if struct.unpack_from('<I',original,no+i*28+12)[0]==0xffffffff]
    visit(roots[0],prior,edited)
    if not changes:return original
    try:
        return _in_place(original,edited)
    except ValueError:
        pass
    # Append-only string pool extension: never reorder or move the original tables.
    if so+sn!=len(original):
        raise ValueError('Cannot extend this XML: string table is not the final file section.')
    out=bytearray(original);interned={}
    for pointer,value in changes:
        encoded=value.encode('utf-8')
        if b'\0' in encoded:raise ValueError('NUL characters are not supported.')
        if encoded not in interned:
            interned[encoded]=len(out)-so
            out.extend(encoded+b'\0')
        struct.pack_into('<I',out,pointer,interned[encoded])
    if len(out)>=2**32:raise ValueError('Binary XML exceeds 32-bit size limits.')
    struct.pack_into('<I',out,8,len(out))
    struct.pack_into('<I',out,40,len(out)-so)
    result=bytes(out)
    if semantic(decode_cryxml(result))!=semantic(edited):
        raise ValueError('Extended-string XML verification failed; output rejected.')
    # Original strings are untouched, including strings shared with unrelated fields.
    assert result[so:so+sn]==original[so:so+sn]
    print('XML: isolated edited strings; original table positions preserved (in-game validation required).')
    return result
