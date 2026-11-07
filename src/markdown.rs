use crate::theme::*;
use gpui_kit::base::Button;
use gpui_kit::base::text::{
    CodeBlock, MarkdownNode, MarkdownParseContext, MarkdownPlugin, TextView, TextViewStyle,
    markdown_ast,
};
use gpui_kit::*;

const CODE_INSET: f32 = 12.;

pub fn code_header(block: &CodeBlock, width: Pixels, window: &Window) -> Div {
    let code = block.code();
    // shortcut: the action slot is right-aligned, use the chat content width for full-width blocks.
    row()
        .w((width - px(CODE_INSET + 1.) - window.rem_size() / 2.).max(px(0.)))
        .justify_between()
        .child(muted(block.lang().unwrap_or_default(), 14.).font(body_font()))
        .child(
            Button::new("copy-code")
                .accessibility_label("复制代码")
                .size(px(26.))
                .flex()
                .items_center()
                .justify_center()
                .rounded(px(DETAIL_RADIUS))
                .child(icon("copy-01", 16., MUTED))
                .on_click(move |_, _, cx| {
                    cx.write_to_clipboard(ClipboardItem::new_string(code.to_string()));
                }),
        )
}

struct Semibold;

impl MarkdownPlugin for Semibold {
    fn name(&self) -> &str {
        "semibold"
    }

    fn parse(
        &self,
        node: &markdown_ast::Node,
        cx: &MarkdownParseContext<'_>,
    ) -> Option<MarkdownNode> {
        match node {
            markdown_ast::Node::Strong(strong)
                if strong
                    .children
                    .iter()
                    .all(|child| matches!(child, markdown_ast::Node::Text(_))) =>
            {
                Some(
                    MarkdownNode::new(self.name(), ())
                        .text(node.to_string())
                        .markdown(cx.node_source(node).unwrap_or_default().to_owned()),
                )
            }
            _ => None,
        }
    }

    fn render(&self, node: &MarkdownNode, _window: &mut Window, _cx: &mut App) -> impl IntoElement {
        // shortcut: static emphasis is atomic; use a text-run renderer for wrapping rich emphasis.
        div().font(emphasis_font()).child(node.as_text().to_owned())
    }
}

pub fn markdown_surface(view: TextView) -> Div {
    div()
        .relative()
        .child(view)
        // GPUI renders links with a hand cursor inside its Markdown element.
        .child(div().absolute().inset_0().cursor_default())
}

pub fn markdown(id: &'static str, source: &'static str) -> TextView {
    let style = TextViewStyle::default()
        .with_foreground(rgb(INK).into())
        .with_muted_foreground(rgb(MUTED).into())
        .with_link(rgb(PRIMARY).into())
        .with_border(rgb(BORDER).into())
        .with_code_background(rgb(FIELD).into())
        .with_paragraph_gap(rems(0.5))
        .with_table(
            StyleRefinement::default()
                .rounded(px(CONTROL_RADIUS))
                .overflow_hidden(),
        )
        .with_inline_code_style(
            StyleRefinement::default()
                .font(code_font())
                .text_size(px(14.)),
        )
        .with_heading(|level| {
            let size = match level {
                1 => 20.,
                2 => 18.,
                _ => 16.,
            };
            StyleRefinement::default()
                .text_size(px(size))
                .font(emphasis_font())
                .pt(px(0.))
                .pb(px(8.))
                .mt(px(0.))
                .mb(px(0.))
        })
        .with_list_style(StyleRefinement::default().text_color(rgb(PRIMARY)))
        .with_blockquote_style(
            StyleRefinement::default()
                .border_0()
                .px(px(8.))
                .py(px(4.))
                .rounded(px(DETAIL_RADIUS))
                .bg(rgb(FIELD))
                .text_color(rgb(MUTED)),
        )
        .with_code_block(
            StyleRefinement::default()
                .font(code_font())
                .text_size(px(14.))
                .p(px(CODE_INSET))
                .pt(px(36.))
                .rounded(px(CONTROL_RADIUS))
                .bg(rgb(FIELD)),
        );
    TextView::markdown(id, source)
        .style(style)
        .plugin(Semibold)
        .selectable(true)
}
