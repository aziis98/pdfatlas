#version 330 core
in vec2 TexCoord;
in vec2 TexCoord2;
out vec4 FragColor;

uniform sampler2D u_texture;
uniform sampler2D u_texture_hl;
uniform int u_is_placeholder;
uniform vec4 u_color;
uniform vec2 u_page_size;
uniform float u_radius;
uniform int u_night_mode;
uniform float u_invert_amount;
uniform int u_hue_rotate;

vec3 apply_night_mode(vec3 c) {
    vec3 inv = mix(c, vec3(1.0) - c, u_invert_amount);
    if (u_hue_rotate == 1) {
        // W3C CSS filter hue-rotate(180deg) column-major matrix:
        mat3 m = mat3(
            -0.574,  0.426,  0.426,
             1.430,  0.430,  1.430,
             0.144,  0.144, -0.856
        );
        inv = clamp(m * inv, 0.0, 1.0);
    }
    return inv;
}

void main() {
    if (u_is_placeholder == 1) {
        vec3 col = vec3(1.0, 1.0, 1.0);
        if (u_night_mode == 1) {
            col = apply_night_mode(col);
        }
        FragColor = vec4(col, 1.0);
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
    } else if (u_is_placeholder == 6) {
        // Page-aware multiply in light mode; alpha-glow blend in night mode.
        vec4 page = texture(u_texture, TexCoord);
        vec4 hl = texture(u_texture_hl, TexCoord2);
        if (u_night_mode == 1) {
            vec3 night_page = apply_night_mode(page.rgb);
            vec3 hl_col = hl.a > 0.001 ? (hl.rgb / hl.a) : vec3(1.0, 0.933, 0.333);
            vec3 final_col = mix(night_page, hl_col, hl.a * 0.42);
            FragColor = vec4(final_col, 1.0);
        } else {
            vec3 col_multiply = vec3(1.0 - hl.a) + hl.rgb;
            float g = (max(page.r, max(page.g, page.b)) - min(page.r, min(page.g, page.b))) * 3.0;
            vec3 tint = mix(col_multiply, vec3(1.0), clamp(g, 0.0, 1.0));
            FragColor = vec4(tint, 1.0);
        }
    } else {
        vec4 tex = texture(u_texture, TexCoord);
        vec3 col = tex.rgb;
        if (u_night_mode == 1) {
            col = apply_night_mode(col);
        }
        FragColor = vec4(col, 1.0);
    }
}

