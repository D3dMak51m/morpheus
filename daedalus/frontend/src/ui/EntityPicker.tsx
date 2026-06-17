import { Modal } from '@mantine/core';
import { DataView, Col } from './DataView';

interface EntityPickerProps<T> {
  opened: boolean;
  onClose: () => void;
  title: string;
  columns: Col<T>[];
  rows: T[];
  rowKey: (row: T) => string | number;
  searchText?: (row: T) => string;
  searchPlaceholder?: string;
  emptyText?: string;
  onPick: (row: T) => void;
}

/**
 * Pick-from-list: a searchable / sortable list (with detailed columns) for choosing an
 * entity, replacing every "type the ID by hand" input. It's a transient selection surface
 * (a focused modal), not an edit screen — editing stays full-screen per the mandate.
 */
export function EntityPicker<T>({
  opened, onClose, title, columns, rows, rowKey, searchText, searchPlaceholder, emptyText, onPick,
}: EntityPickerProps<T>) {
  return (
    <Modal opened={opened} onClose={onClose} title={title} size="xl" centered
      styles={{ body: { paddingTop: 8 } }}>
      <DataView
        columns={columns}
        rows={rows}
        rowKey={rowKey}
        searchText={searchText}
        searchPlaceholder={searchPlaceholder}
        emptyText={emptyText}
        pageSize={10}
        onRowClick={(row) => { onPick(row); onClose(); }}
      />
    </Modal>
  );
}

export default EntityPicker;
