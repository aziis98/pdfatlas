#version 330 core
layout (location = 0) in vec2 aPos;
layout (location = 1) in vec2 aTexCoord;

out vec2 TexCoord;

uniform vec2 u_resolution;
uniform vec2 u_offset;
uniform vec2 u_page_pos;
uniform vec2 u_page_size;
uniform float u_flip_v;

void main() {
    vec2 pixel_pos = u_page_pos + aPos * u_page_size - u_offset;
    vec2 ndc_pos;
    ndc_pos.x = (pixel_pos.x / u_resolution.x) * 2.0 - 1.0;
    ndc_pos.y = 1.0 - (pixel_pos.y / u_resolution.y) * 2.0;

    gl_Position = vec4(ndc_pos, 0.0, 1.0);
    TexCoord = vec2(aTexCoord.x, mix(aTexCoord.y, 1.0 - aTexCoord.y, u_flip_v));
}
