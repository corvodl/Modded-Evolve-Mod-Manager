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


def parse_motion(texture):
    """Read *named* constant-pan/rotation TexMod motion, seconds-based.

    Only textual Constant/Pan modes are supported. Numeric engine enum values,
    jitter, stretch and unknown oscillators are intentionally not guessed.
    Rates use their literal XML values; rotation rate is degrees/second.
    """
    node = texture.find('TexMod') if texture is not None else None
    if node is None:
        return (0.,0.,0.)
    motion = []
    for axis in ('U','V'):
        mode = (node.get('TexMod_' + axis + 'OscillatorType') or '').casefold()
        if mode not in ('constant','pan','scroll'):
            motion.append(0.)
            continue
        value = float(node.get('TexMod_' + axis + 'OscillatorRate','0'))
        if not math.isfinite(value) or abs(value)>10000:
            raise ValueError('Invalid animated texture rate for ' + axis)
        motion.append(value)
    mode = (node.get('TexMod_RotateType') or '').casefold()
    rate = (float(node.get('TexMod_RotateRate','0'))
            if mode in ('constant','pan','scroll') else 0.)
    if not math.isfinite(rate) or abs(rate)>10000:
        raise ValueError('Invalid animated rotation rate.')
    return (motion[0],motion[1],rate)


def transform_for(materials, slot, role='diffuse', seconds=0.):
    """Read independent TexMod plus supported named constant motion."""
    if role != 'diffuse':
        pairs = getattr(materials, 'map_uv_transforms', ())
        values = next((v for name,v in pairs if name == role),())
        base = values[slot] if 0 <= slot < len(values) else IDENTITY
        motions = next((v for name,v in getattr(materials, 'map_uv_motions', ())
                        if name == role),())
    else:
        values = getattr(materials, 'uv_transforms', ())
        base = values[slot] if 0 <= slot < len(values) else IDENTITY
        motions = getattr(materials, 'uv_motions', ())
    if seconds and 0 <= slot < len(motions):
        from math import radians
        du, dv, degrees = motions[slot]
        if any((du,dv,degrees)):
            su,sv,ou,ov,angle=base
            return (su,sv,(ou+du*seconds)%1.,(ov+dv*seconds)%1.,
                    (angle+radians(degrees*seconds))%(2*math.pi))
    return base


def gl_matrix3(transform):
    """Column-major 3x3 UV matrix for independent GLSL material samplers."""
    su, sv, ou, ov, angle = transform
    c, s = math.cos(angle), math.sin(angle)
    return (c*su, s*su, 0., -s*sv, c*sv, 0., ou, ov, 1.)


def transform_uv(u, v, transform):
    su, sv, ou, ov, angle = transform
    c, s = math.cos(angle), math.sin(angle)
    return c*su*u-s*sv*v+ou, s*su*u+c*sv*v+ov


def gl_matrix(transform):
    su, sv, ou, ov, angle = transform
    c, s = math.cos(angle), math.sin(angle)
    return (c*su,s*su,0,0, -s*sv,c*sv,0,0, 0,0,1,0, ou,ov,0,1)
