import React from 'react';
import { X } from 'lucide-react';
import './SidePanel.css';

interface SidePanelProps {
  open: boolean;
  title: string;
  subtitle?: string;
  onClose: () => void;
  /** Sticky footer (e.g. Cancel / Save buttons). */
  footer?: React.ReactNode;
  children: React.ReactNode;
  width?: number;
}

/**
 * Non-blocking edit surface that slides in from the right. Unlike a modal
 * overlay it does NOT dim/capture the rest of the page, so the operator can
 * still use the sidebar to switch views without closing the editor (its React
 * state — and any unsaved edits — survive while the host view is display:none).
 */
export function SidePanel({ open, title, subtitle, onClose, footer, children, width = 440 }: SidePanelProps) {
  if (!open) return null;
  return (
    <aside className="side-panel" style={{ width }} role="dialog" aria-label={title}>
      <div className="side-panel-header">
        <div>
          <h2>{title}</h2>
          {subtitle && <p className="side-panel-subtitle">{subtitle}</p>}
        </div>
        <button className="side-panel-close" onClick={onClose} title="Закрыть"><X size={18} /></button>
      </div>
      <div className="side-panel-body">{children}</div>
      {footer && <div className="side-panel-footer">{footer}</div>}
    </aside>
  );
}

export default SidePanel;
