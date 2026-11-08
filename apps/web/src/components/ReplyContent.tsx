// 将助手回复排版为可阅读的段落、列表与表格，保留旧记录中的换行。
import { memo } from "react";
import Markdown, { type Components } from "react-markdown";
import remarkBreaks from "remark-breaks";
import remarkGfm from "remark-gfm";

const plugins = [remarkGfm, remarkBreaks];
const components: Components = {
  a: ({ href, title, children }) => href
    ? <a href={href} title={title} target="_blank" rel="noopener noreferrer">{children}</a>
    : <span>{children}</span>,
  // 模型提供的图片只展示说明，不自动请求外部资源。
  img: ({ alt }) => <span className="reply-image-label">{alt}</span>,
  table: ({ children }) => (
    <div className="reply-table" role="region" aria-label="回复中的表格" tabIndex={0}>
      <table>{children}</table>
    </div>
  ),
};

/** 渲染只读回复；播放头变化时复用排版结果，避免反复解析长消息。 */
export const ReplyContent = memo(function ReplyContent({ text, failed = false }: {
  text: string;
  failed?: boolean;
}) {
  return (
    <div className={`reply-content${failed ? " failure-text" : ""}`}>
      <Markdown remarkPlugins={plugins} components={components} skipHtml>{text}</Markdown>
    </div>
  );
});
