"""Enquadra palette with native GTK controls and system theme selection."""

CSS = """
.brio-window { background: @enquadra_canvas; color: @enquadra_text; }
.brio-window headerbar { min-height: 46px; box-shadow: none; border-bottom: 1px solid alpha(@enquadra_text, .08); }
.brio-window .brand-title { font-size: 16px; font-weight: 700; }
.brio-window .brand-subtitle, .brio-window .caption { font-size: 11px; color: @enquadra_muted; }
.brio-window .workspace-title { font-size: 18px; font-weight: 700; }
.brio-window .section-title { font-size: 13px; font-weight: 700; }
.brio-window .helper { font-size: 12px; color: @enquadra_muted; }
.brio-window .camera-bar { padding: 12px; border-radius: 12px; background: alpha(@enquadra_text, .035); border: 1px solid alpha(@enquadra_text, .08); }
.brio-window .camera-bar dropdown { min-width: 140px; }
.brio-window .primary-action { background-image: none; min-height: 32px; padding: 4px 16px; border-radius: 8px; background: @enquadra_primary; color: @enquadra_primary_text; font-weight: 600; border: 1px solid @enquadra_primary; }
.brio-window .primary-action:hover { background: shade(@enquadra_primary, .92); }
.brio-window .stop-action { background: alpha(@enquadra_text, .06); color: @enquadra_text; border-color: alpha(@enquadra_text, .18); }
.brio-window .stop-action:hover { background: alpha(@enquadra_text, .12); }
.brio-window .status-pill { border-radius: 20px; padding: 4px 10px; background: alpha(@enquadra_text, .08); font-size: 11px; font-weight: 600; }
.brio-window .status-pill.live { background: alpha(#27D3C2, .17); color: @enquadra_text; }
.brio-window .preview-surface { background: #102C36; color: #F6F3EB; border: 1px solid alpha(@enquadra_text, .12); border-radius: 14px; }
.brio-window .preview-empty-icon { color: #A8E6D5; }
.brio-window .preview-empty-title { font-size: 20px; font-weight: 600; color: #F6F3EB; }
.brio-window .preview-empty-text { color: #A8E6D5; font-size: 12px; }
.brio-window .preview-empty { padding: 24px; }
.brio-window .preview-meta { color: #A8E6D5; background: alpha(#102C36, .80); border-radius: 6px; padding: 5px 9px; font-size: 11px; }
.brio-window .inspector { background: alpha(@enquadra_text, .025); border: 1px solid alpha(@enquadra_text, .09); border-radius: 12px; }
.brio-window .inspector-nav { padding: 6px; border-bottom: 1px solid alpha(@enquadra_text, .08); }
.brio-window .inspector-nav button { background: transparent; box-shadow: none; border: none; padding: 8px 4px; border-radius: 7px; font-size: 11px; }
.brio-window .inspector-nav button:checked { background: alpha(@enquadra_primary, .15); color: @enquadra_text; font-weight: 700; }
.brio-window .inspector-nav button:hover { background: alpha(@enquadra_text, .07); }
.brio-window .settings-page { padding: 18px; }
.brio-window .control-row { padding: 10px 0; }
.brio-window .control-row + .control-row { border-top: 1px solid alpha(@enquadra_text, .06); }
.brio-window .control-value { font-size: 12px; color: @enquadra_muted; font-feature-settings: 'tnum'; }
.brio-window .control-row scale { padding: 4px 0; min-height: 18px; }
.brio-window .control-row scale trough { min-height: 4px; }
.brio-window .control-row scale highlight { background: @enquadra_primary; }
.brio-window .control-row dropdown { margin-top: 6px; }
.brio-window .group-title { margin-top: 18px; margin-bottom: 4px; font-weight: 700; font-size: 12px; opacity: .8; }
.brio-window .group-title.first { margin-top: 0; }
.brio-window .info-strip { padding: 12px; border-radius: 10px; background: alpha(@enquadra_text, .035); }
.brio-window .feedback-banner { padding: 12px; border-radius: 10px; background: alpha(#F2B669, .12); border: 1px solid alpha(#F2B669, .3); }
.brio-window .feedback-title { font-weight: 600; }
.brio-window .feedback-banner.error-banner { background: alpha(@enquadra_error, .1); border-color: alpha(@enquadra_error, .25); }
.brio-window .profile-card { padding: 14px; background: alpha(@enquadra_text, .035); border-radius: 10px; }
.brio-window .flat-action { padding: 4px 8px; font-size: 12px; }
.brio-window .page-title { font-size: 18px; font-weight: 700; }
.brio-window .inspector switch:checked { background: @enquadra_primary; }
.brio-window button:focus-visible, .brio-window dropdown:focus-visible, .brio-window entry:focus-visible, .brio-window scale:focus-visible, .brio-window checkbutton:focus-visible, .brio-window switch:focus-visible { outline: 2px solid @enquadra_primary; outline-offset: 2px; }
"""


def stylesheet(dark: bool) -> str:
    """Select explicit readable roles without overriding native control surfaces."""
    colors = ("#102C36", "#F6F3EB", "#A8E6D5", "#27D3C2", "#102C36", "#FFB4AB") if dark else (
        "#F6F3EB", "#102C36", "#52696D", "#087D78", "#FFFFFF", "#A3413D")
    roles = ("canvas", "text", "muted", "primary", "primary_text", "error")
    return "\n".join(f"@define-color enquadra_{role} {color};"
                     for role, color in zip(roles, colors)) + "\n" + CSS
