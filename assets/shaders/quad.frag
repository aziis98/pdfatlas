#version 330 core
in vec2 TexCoord;
out vec4 FragColor;

uniform sampler2D u_texture;
uniform int u_is_placeholder;
uniform vec4 u_color;
uniform vec2 u_page_size;
uniform float u_radius;

void main() {
    if (u_is_placeholder == 1) {
        FragColor = vec4(1.0, 1.0, 1.0, 1.0);
    } else if (u_is_placeholder == 2) {
        FragColor = u_color;
    } else if (u_is_placeholder == 3) {
        vec2 p = (TexCoord - 0.5) * u_page_size;
        vec2 b = u_page_size * 0.5 - u_radius;
        vec2 q = max(abs(p) - b, vec2(0.0));
        float d = length(q) - u_radius;
        float cov = clamp(0.5 - d, 0.0, 1.0);
        float alpha = u_color.a * cov;
        FragColor = vec4(u_color.rgb * alpha, alpha);
    } else if (u_is_placeholder == 4) {
        vec4 tex = texture(u_texture, TexCoord);
        FragColor = vec4(tex.rgb * 0.5, tex.a * 0.5);
    } else if (u_is_placeholder == 5) {
        vec4 tex = texture(u_texture, TexCoord);
        float a = tex.a;
        vec3 rgb = a > 0.001 ? tex.rgb / a : vec3(1.0);
        vec3 col = mix(vec3(1.0), rgb, a);
        FragColor = vec4(col, 1.0);
    } else {
        vec4 tex = texture(u_texture, TexCoord);
        FragColor = vec4(tex.rgb, 1.0);
    }
}
