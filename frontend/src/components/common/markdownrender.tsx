import React, { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Prism, SyntaxHighlighterProps } from "react-syntax-highlighter";
import { tomorrow } from "react-syntax-highlighter/dist/esm/styles/prism";

const SyntaxHighlighter = Prism as any as React.FC<SyntaxHighlighterProps>;

interface MarkdownRendererProps {
  content: string;
  fileExtension?: string;
  truncate?: boolean;
  maxLength?: number;
  indented?: boolean;
}

// Map file extensions to syntax highlighting languages
const extensionToLanguage: Record<string, string> = {
  js: "javascript",
  jsx: "jsx",
  ts: "typescript",
  tsx: "tsx",
  py: "python",
  rb: "ruby",
  java: "java",
  c: "c",
  cpp: "cpp",
  cs: "csharp",
  go: "go",
  php: "php",
  html: "html",
  css: "css",
  json: "json",
  md: "markdown",
  sql: "sql",
  sh: "bash",
  bash: "bash",
  yaml: "yaml",
  yml: "yaml",
  xml: "xml",
  txt: "text",
};

const CodeBlock: React.FC<{ language: string; value: string }> = ({
  language,
  value,
}) => {
  const [copied, setCopied] = useState(false);
  const [expanded, setExpanded] = useState(false);

  const handleCopy = async () => {
    await navigator.clipboard.writeText(value);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  // Split code into lines
  const lines = value.split("\n");
  const isLong = lines.length > 20;
  const displayedValue =
    !expanded && isLong ? lines.slice(0, 20).join("\n") : value;

  return (
    <div style={{ position: "relative", marginBottom: "1rem" }}>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          padding: "0.5rem 1rem",
          backgroundColor: "var(--color-bg-secondary)",
          borderTopLeftRadius: "0.375rem",
          borderTopRightRadius: "0.375rem",
          borderBottom: "1px solid var(--color-border-secondary)",
        }}
      >
        <span
          style={{ color: "var(--color-text-secondary)", fontSize: "0.9rem" }}
        >
          {language || "text"}
        </span>
        <button
          onClick={handleCopy}
          style={{
            background: "transparent",
            border: "none",
            color: "var(--color-text-secondary)",
            cursor: "pointer",
            padding: "0.25rem 0.5rem",
            fontSize: "0.9rem",
            transition: "color 0.2s",
          }}
          onMouseEnter={(e) =>
            (e.currentTarget.style.color = "var(--color-text-primary)")
          }
          onMouseLeave={(e) =>
            (e.currentTarget.style.color = "var(--color-text-secondary)")
          }
        >
          {copied ? "Copied!" : "Copy"}
        </button>
      </div>
      <div style={{ backgroundColor: "#000" }}>
        <SyntaxHighlighter
          style={tomorrow}
          language={language || "text"}
          PreTag="div"
          customStyle={{
            backgroundColor: "#000",
            margin: 0,
            borderBottomLeftRadius: "0.375rem",
            borderBottomRightRadius: "0.375rem",
            padding: "1rem",
          }}
        >
          {displayedValue}
        </SyntaxHighlighter>
        {isLong && (
          <div style={{ textAlign: "center", marginTop: "0.5rem" }}>
            <button
              onClick={() => setExpanded((prev) => !prev)}
              style={{
                background: "transparent",
                border: "none",
                color: "var(--color-text-secondary)",
                cursor: "pointer",
                padding: "0.25rem 0.5rem",
                fontSize: "0.9rem",
                transition: "color 0.2s",
              }}
              onMouseEnter={(e) =>
                (e.currentTarget.style.color = "var(--color-text-primary)")
              }
              onMouseLeave={(e) =>
                (e.currentTarget.style.color = "var(--color-text-secondary)")
              }
            >
              {expanded ? "Show less" : `Show ${lines.length - 20} more lines`}
            </button>
          </div>
        )}
      </div>
    </div>
  );
};

const MarkdownRenderer: React.FC<MarkdownRendererProps> = ({
  content,
  fileExtension,
  truncate,
  maxLength,
  indented = false,
}) => {
  // Determine if we should render as a file preview
  const isFilePreview = !!fileExtension;
  const color = indented
    ? "var(--color-text-secondary)"
    : "var(--color-text-primary)";

  // If this is a file preview, wrap the content in a code block
  const processedContent = isFilePreview
    ? `\`\`\`${
        extensionToLanguage[fileExtension?.toLowerCase() || ""] || "text"
      }\n${content}\n\`\`\``
    : content;

  // Truncate content if needed
  const truncatedContent =
    truncate && maxLength && content.length > maxLength
      ? content.slice(0, maxLength) + "..."
      : content;

  // 检查是否是纯文本（不包含 Markdown 语法）
  // 如果是纯文本且包含大量中文，直接渲染为普通段落
  const isPlainText = !fileExtension && 
                      /[\u4e00-\u9fa5]/.test(content) && 
                      !content.match(/^#{1,6}\s/) && // 不是标题
                      !content.match(/^\s*[-*+]\s/) && // 不是列表
                      !content.match(/^\s*>\s/) && // 不是引用
                      !content.match(/```/) && // 不包含代码块标记
                      !content.match(/^```/m); // 不是代码块开头

  // 如果是纯文本，直接渲染为段落，不经过 Markdown 解析
  if (isPlainText) {
    return (
      <div
        className="prose w-full "
        style={{
          color,
          fontSize: "0.85rem",
          overflowWrap: "break-word",
          wordWrap: "break-word",
          wordBreak: "break-word",
          overflowX: "auto",
          maxWidth: "100%",
          position: "relative",
        }}
      >
        {indented && (
          <div
            style={{
              position: "absolute",
              left: "1.2rem",
              top: 0,
              bottom: 0,
              width: "2px",
            }}
          />
        )}
        <p style={{ color, whiteSpace: "pre-wrap" }}>{content}</p>
      </div>
    );
  }

  return (
    <div
      className="prose w-full "
      style={{
        color,
        fontSize: "0.85rem",
        overflowWrap: "break-word",
        wordWrap: "break-word",
        wordBreak: "break-word",
        overflowX: "auto",
        maxWidth: "100%",
        position: "relative",
      }}
    >
      {indented && (
        <div
          style={{
            position: "absolute",
            left: "1.2rem",
            top: 0,
            bottom: 0,
            width: "2px",
          }}
        />
      )}
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          h1: ({ children }) => <h1 style={{ color }}>{children}</h1>,
          h2: ({ children }) => <h2 style={{ color }}>{children}</h2>,
          h3: ({ children }) => <h3 style={{ color }}>{children}</h3>,
          h4: ({ children }) => <h4 style={{ color }}>{children}</h4>,
          h5: ({ children }) => <h5 style={{ color }}>{children}</h5>,
          h6: ({ children }) => <h6 style={{ color }}>{children}</h6>,
          p: ({ children }) => (
            <p className="" style={{ color }}>
              {children}
            </p>
          ),
          strong: ({ children }) => (
            <strong style={{ color }}>{children}</strong>
          ),
          a: ({ href, children }) => (
            <a
              href={href}
              style={{ color }}
              target="_blank"
              rel="noopener noreferrer"
            >
              {children}
            </a>
          ),
          code: ({ node, className, children, ...props }) => {
            const match = /language-(\w+)/.exec(className || "");
            const language = match ? match[1] : "";
            const childrenStr = String(children);
            
            // 检查是否是真正的代码块
            // 有语言标识，或者是多行且明显是代码内容（包含常见代码特征）
            const hasCodeMarkers = /[{};=]/.test(childrenStr) || 
                                   /^(import|export|def|function|class|return|const|let|var)\s/.test(childrenStr.trim());
            const isCodeBlock = !!language || (childrenStr.includes('\n') && hasCodeMarkers);
            
            if (!isCodeBlock) {
              // 如果不在代码块中，检查是否包含中文、句号、问号等，如果是，说明是普通文本
              // 应该渲染为普通段落而不是代码
              const isNormalText = /[\u4e00-\u9fa5]/.test(childrenStr) || 
                                   /[。？！，；：]/.test(childrenStr) ||
                                   !/[{}[\]]/.test(childrenStr);
              
              if (isNormalText && !language) {
                // 普通文本，直接渲染为段落
                return <p style={{ color, whiteSpace: "pre-wrap" }}>{children}</p>;
              }
              
              // 内联代码
              return (
                <code
                  style={{
                    whiteSpace: "pre-wrap",
                    color: "var(--color-text-primary)",
                    backgroundColor: "var(--color-bg-primary)",
                    display: "inline",
                    padding: "0.2em 0.4em",
                    borderRadius: "0.375rem",
                  }}
                  {...props}
                >
                  {children}
                </code>
              );
            }

            return (
              <CodeBlock
                language={language}
                value={childrenStr.replace(/\n$/, "")}
              />
            );
          },
          blockquote: ({ children }) => (
            <blockquote
              style={{
                backgroundColor: "var(--color-bg-primary)",
                color: "var(--color-text-primary)",
                padding: "10px",
                borderLeft: "5px solid var(--color-border-secondary)",
              }}
            >
              {children}
            </blockquote>
          ),
        }}
      >
        {truncate ? truncatedContent : processedContent}
      </ReactMarkdown>
    </div>
  );
};

export default MarkdownRenderer;
