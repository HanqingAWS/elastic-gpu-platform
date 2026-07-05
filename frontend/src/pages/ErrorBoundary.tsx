import { Component, ReactNode } from 'react';

// 单个页面渲染出错时,只在内容区显示回退,不再整站白屏。
export default class ErrorBoundary extends Component<
  { children: ReactNode; resetKey?: string },
  { err: Error | null }
> {
  state = { err: null as Error | null };

  static getDerivedStateFromError(err: Error) {
    return { err };
  }
  componentDidUpdate(prev: { resetKey?: string }) {
    if (prev.resetKey !== this.props.resetKey && this.state.err) this.setState({ err: null });
  }

  render() {
    if (this.state.err) {
      return (
        <div className="card">
          <h3>页面出错</h3>
          <p className="muted" style={{ fontSize: 13.5, margin: '6px 0 12px' }}>
            该页面渲染时抛出异常,已被隔离(其他页面不受影响)。
          </p>
          <div className="arn" style={{ marginBottom: 14 }}>{String(this.state.err?.message || this.state.err)}</div>
          <button className="btn btn-sm" onClick={() => location.reload()}>刷新页面</button>
        </div>
      );
    }
    return this.props.children;
  }
}
