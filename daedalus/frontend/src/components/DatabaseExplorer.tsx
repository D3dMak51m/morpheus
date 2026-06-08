import React, { useState, useEffect } from 'react';
import './DatabaseExplorer.css';

interface DatabaseExplorerProps {
  token: string;
}

interface TableData {
  table: string;
  columns: string[];
  rows: Record<string, any>[];
  total_count: number;
}

const DatabaseExplorer: React.FC<DatabaseExplorerProps> = ({ token }) => {
  const [tables, setTables] = useState<string[]>([]);
  const [selectedTable, setSelectedTable] = useState<string>('');
  const [tableData, setTableData] = useState<TableData | null>(null);
  
  const [limit] = useState(100);
  const [offset, setOffset] = useState(0);
  
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  
  // SQL Console
  const [sqlQuery, setSqlQuery] = useState('');
  const [sqlResult, setSqlResult] = useState<any>(null);
  const [sqlError, setSqlError] = useState('');
  
  // Edit mode
  const [editingCell, setEditingCell] = useState<{rowIndex: number, col: string} | null>(null);
  const [editValue, setEditValue] = useState('');

  const headers = {
    'Content-Type': 'application/json',
    'Authorization': `Bearer ${token}`,
  };

  useEffect(() => {
    fetchTables();
  }, []);

  useEffect(() => {
    if (selectedTable) {
      fetchTableData(selectedTable, limit, offset);
    }
  }, [selectedTable, limit, offset]);

  const fetchTables = async () => {
    try {
      const res = await fetch('/api/v1/db/tables', { headers });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setTables(data.tables || []);
      if (data.tables && data.tables.length > 0) {
        setSelectedTable(data.tables[0]);
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to fetch tables');
    }
  };

  const fetchTableData = async (tableName: string, l: number, o: number) => {
    setLoading(true);
    setError('');
    try {
      const res = await fetch(`/api/v1/db/tables/${tableName}?limit=${l}&offset=${o}`, { headers });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      setTableData(data);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to fetch table data');
      setTableData(null);
    } finally {
      setLoading(false);
    }
  };

  const startEdit = (rowIndex: number, col: string, val: any) => {
    setEditingCell({ rowIndex, col });
    setEditValue(val === null ? '' : String(val));
  };

  const saveCell = async (rowIndex: number, col: string) => {
    if (!tableData) return;
    const row = tableData.rows[rowIndex];
    const pkCol = tableData.columns[0]; // Assuming first column is PK (usually 'id')
    const pkVal = row[pkCol];

    try {
      const res = await fetch('/api/v1/db/cell', {
        method: 'PUT',
        headers,
        body: JSON.stringify({
          table: selectedTable,
          primary_key_column: pkCol,
          primary_key_value: pkVal,
          column: col,
          new_value: editValue
        })
      });
      
      if (!res.ok) {
        const errData = await res.json().catch(() => null);
        throw new Error(errData?.detail || `HTTP ${res.status}`);
      }
      
      // Update local state
      const newRows = [...tableData.rows];
      newRows[rowIndex] = { ...newRows[rowIndex], [col]: editValue };
      setTableData({ ...tableData, rows: newRows });
      
      setEditingCell(null);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to update cell');
    }
  };

  const executeSql = async () => {
    setSqlError('');
    setSqlResult(null);
    if (!sqlQuery.trim()) return;
    
    try {
      const res = await fetch('/api/v1/db/query', {
        method: 'POST',
        headers,
        body: JSON.stringify({ sql: sqlQuery })
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
      setSqlResult(data);
    } catch (e: unknown) {
      setSqlError(e instanceof Error ? e.message : 'Query failed');
    }
  };

  return (
    <div className="database-explorer view-container">
      <div className="header-row">
        <div>
          <h1>Database Explorer</h1>
          <p className="subtitle">Direct PostgreSQL table access and SQL execution.</p>
        </div>
      </div>

      {error && <div className="error-banner">{error}</div>}

      <div className="explorer-layout">
        <div className="table-sidebar">
          <h3>Tables</h3>
          <ul className="table-list">
            {tables.map(t => (
              <li 
                key={t} 
                className={t === selectedTable ? 'active' : ''}
                onClick={() => { setSelectedTable(t); setOffset(0); }}
              >
                {t}
              </li>
            ))}
          </ul>
        </div>
        
        <div className="table-content">
          <div className="sql-console">
            <h3>Raw SQL Console (SuperAdmin)</h3>
            <textarea 
              value={sqlQuery} 
              onChange={e => setSqlQuery(e.target.value)} 
              placeholder="SELECT * FROM roles WHERE id = 1;"
              rows={4}
            />
            <button className="btn-primary" onClick={executeSql}>Execute Query</button>
            
            {sqlError && <div className="error-banner" style={{marginTop: '10px'}}>{sqlError}</div>}
            
            {sqlResult && (
              <div className="sql-result">
                <p className="text-muted">Rows returned: {sqlResult.row_count}</p>
                {sqlResult.rows.length > 0 && (
                  <div className="data-grid-container" style={{maxHeight: '300px'}}>
                    <table className="data-grid">
                      <thead>
                        <tr>
                          {sqlResult.columns.map((c: string) => <th key={c}>{c}</th>)}
                        </tr>
                      </thead>
                      <tbody>
                        {sqlResult.rows.map((r: any, i: number) => (
                          <tr key={i}>
                            {sqlResult.columns.map((c: string) => <td key={c}>{String(r[c])}</td>)}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            )}
          </div>

          <div className="table-view">
            <div className="table-header-controls">
              <h3>{selectedTable} <span className="text-muted">({tableData?.total_count || 0} rows)</span></h3>
              <div className="pagination">
                <button 
                  disabled={offset === 0} 
                  onClick={() => setOffset(Math.max(0, offset - limit))}
                  className="btn-secondary"
                >Prev</button>
                <span className="text-muted">Offset: {offset}</span>
                <button 
                  disabled={!tableData || offset + limit >= tableData.total_count} 
                  onClick={() => setOffset(offset + limit)}
                  className="btn-secondary"
                >Next</button>
              </div>
            </div>

            <div className="data-grid-container">
              {loading ? <p>Loading data...</p> : tableData?.rows.length === 0 ? (
                <p className="empty-state">Table is empty.</p>
              ) : (
                <table className="data-grid">
                  <thead>
                    <tr>
                      {tableData?.columns.map(c => <th key={c}>{c}</th>)}
                    </tr>
                  </thead>
                  <tbody>
                    {tableData?.rows.map((row, rowIndex) => (
                      <tr key={rowIndex}>
                        {tableData.columns.map(col => {
                          const isEditing = editingCell?.rowIndex === rowIndex && editingCell?.col === col;
                          const val = row[col];
                          // Do not allow editing first column (PK)
                          const isPk = col === tableData.columns[0];

                          return (
                            <td key={col} onDoubleClick={() => { if(!isPk) startEdit(rowIndex, col, val); }}>
                              {isEditing ? (
                                <input
                                  autoFocus
                                  value={editValue}
                                  onChange={e => setEditValue(e.target.value)}
                                  onBlur={() => saveCell(rowIndex, col)}
                                  onKeyDown={e => {
                                    if (e.key === 'Enter') saveCell(rowIndex, col);
                                    if (e.key === 'Escape') setEditingCell(null);
                                  }}
                                  className="cell-editor"
                                />
                              ) : (
                                <span className={val === null ? 'null-val' : ''}>
                                  {val === null ? 'NULL' : String(val)}
                                </span>
                              )}
                            </td>
                          )
                        })}
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
            <p className="text-muted" style={{marginTop: '10px', fontSize: '0.8rem'}}>Double-click a cell to edit it directly (requires db:edit permission). Primary keys cannot be edited inline.</p>
          </div>
        </div>
      </div>
    </div>
  );
};

export default DatabaseExplorer;
