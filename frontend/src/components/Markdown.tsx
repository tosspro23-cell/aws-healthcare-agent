import ReactMarkdown from "react-markdown";

/** Bedrock's prose includes literal markdown (`**bold**`, numbered lists,
 * headings) that was previously shown as raw text -- found distracting
 * while testing. `react-markdown` never executes raw HTML (it parses to
 * React elements, not `dangerouslySetInnerHTML`), which matters here
 * since this text ultimately originates from an LLM completion, not
 * fully trusted static content, even though the safety pipeline already
 * constrains its factual content. */
export function Markdown({ text }: { text: string }) {
  return (
    <div className="markdown">
      <ReactMarkdown
        components={{
          a: (props) => <a {...props} target="_blank" rel="noreferrer" />,
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}
