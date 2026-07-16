import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach, vi } from 'vitest'

afterEach(() => cleanup())

/**
 * jsdom has no layout engine and no ResizeObserver, which @tanstack/react-virtual
 * needs to decide what to render. Give the scroll container a real size so the
 * virtualizer produces rows — otherwise every list renders empty and a feed test
 * would pass while showing nothing.
 */
class ResizeObserverStub {
  constructor(private readonly cb: ResizeObserverCallback) {}
  observe(target: Element) {
    this.cb(
      [{ target, contentRect: target.getBoundingClientRect() } as unknown as ResizeObserverEntry],
      this as unknown as ResizeObserver,
    )
  }
  unobserve() {}
  disconnect() {}
}

vi.stubGlobal('ResizeObserver', ResizeObserverStub)
// virtual-core resolves the observer off scrollElement.ownerDocument.defaultView,
// which is jsdom's window — not necessarily the same object as globalThis.
;(document.defaultView as unknown as Record<string, unknown>).ResizeObserver = ResizeObserverStub

const VIEWPORT_HEIGHT = 600
const VIEWPORT_WIDTH = 800

/**
 * virtual-core measures the scroll element with `offsetWidth`/`offsetHeight`
 * (getRect in @tanstack/virtual-core), NOT getBoundingClientRect. jsdom reports
 * both as 0 for every element, so without these the virtualizer sees a
 * zero-height viewport and renders no rows at all — a feed test would then pass
 * while displaying nothing.
 */
for (const [prop, value] of [
  ['offsetHeight', VIEWPORT_HEIGHT],
  ['offsetWidth', VIEWPORT_WIDTH],
  ['clientHeight', VIEWPORT_HEIGHT],
  ['clientWidth', VIEWPORT_WIDTH],
  ['scrollHeight', VIEWPORT_HEIGHT],
] as const) {
  Object.defineProperty(HTMLElement.prototype, prop, { configurable: true, get: () => value })
}

HTMLElement.prototype.getBoundingClientRect = function getBoundingClientRect() {
  return {
    x: 0,
    y: 0,
    top: 0,
    left: 0,
    right: 800,
    bottom: VIEWPORT_HEIGHT,
    width: 800,
    height: VIEWPORT_HEIGHT,
    toJSON: () => ({}),
  } as DOMRect
}

HTMLElement.prototype.scrollTo = () => {}
// Same jsdom gap: no layout engine, so no scrollIntoView. The chat transcript pins
// itself to the newest message with it, and an unstubbed call throws inside a commit
// — which surfaces as the whole component failing to render, not as a scroll bug.
HTMLElement.prototype.scrollIntoView = () => {}
