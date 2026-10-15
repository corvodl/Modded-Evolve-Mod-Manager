"""Static diffuse UV transforms shared by the renderer and glTF exporter."""
import math

IDENTITY = (1., 1., 0., 0., 0.)


def parse_transform(texture):
    node = texture.find('TexMod') if texture is not None else None
    if node is None:
        return IDENTITY, False
    def number(key, default):
        value = float(node.get(key, default))
        if not math.isfinite(value) or abs(value) > 10000:
            raise ValueError('Invalid texture transform: ' + key)
        return value
    su, sv = number('TileU', 1), number('TileV', 1)
    u, v = number('OffsetU', 0), number('OffsetV', 0)
    angle = math.radians(number('RotateW', 0))
    cu, cv = number('TexMod_URotateCenter', .5), number('TexMod_VRotateCenter', .5)
    c, s = math.cos(angle), math.sin(angle)
    # Scale/offset first, then rotate about the declared UV center.
    ou, ov = c*(u-cu)-s*(v-cv)+cu, s*(u-cu)+c*(v-cv)+cv
    dynamic = any(number(k, 0) != 0 for k in ('TexMod_UOscillatorType', 'TexMod_VOscillatorType', 'TexMod_RotateType'))
    return (su, sv, ou, ov, angle), dynamic


def transform_for(materials, slot):
    values = getattr(materials, 'uv_transforms', ())
    return values[slot] if 0 <= slot < len(values) else IDENTITY


def transform_uv(u, v, transform):
    su, sv, ou, ov, angle = transform
    c, s = math.cos(angle), math.sin(angle)
    return c*su*u-s*sv*v+ou, s*su*u+c*sv*v+ov


def gl_matrix(transform):
    su, sv, ou, ov, angle = transform
    c, s = math.cos(angle), math.sin(angle)
    return (c*su,s*su,0,0, -s*sv,c*sv,0,0, 0,0,1,0, ou,ov,0,1)
