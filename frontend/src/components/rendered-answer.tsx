"use client";

/**
 * An assistant reply, rendered.
 *
 * Models answer in markdown whether or not you ask them to — headings, lists,
 * bold, fenced code. Printing that raw put literal asterisks and backticks in
 * front of the reader and turned every numbered list into one run-on
 * paragraph.
 *
 * react-markdown escapes HTML rather than rendering it, which is what we want
 * here: the text comes from a model, and a model that has read a document can
 * be talked into emitting a tag by the document. Nothing here opts back into
 * raw HTML.
 *
 * The user's own messages stay plain text — they typed what they typed, and
 * silently reinterpreting someone's asterisks as emphasis is wrong.
 */

import React from "react";
import Markdown from "react-markdown";

export function RenderedAnswer({ content }: { content: string }) {
  return (
    <div className="answer">
      <Markdown
        components={{
          // Links from a model can point anywhere. Open them in a new tab and
          // strip the referrer rather than navigating the app away.
          a: ({ href, children }) => (
            <a href={href} target="_blank" rel="noopener noreferrer nofollow">
              {children}
            </a>
          ),
        }}
      >
        {content}
      </Markdown>
    </div>
  );
}
