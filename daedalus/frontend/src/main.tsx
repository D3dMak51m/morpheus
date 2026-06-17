import React from 'react'
import ReactDOM from 'react-dom/client'
import '@mantine/core/styles.css'
import { MantineProvider, createTheme } from '@mantine/core'
import App from './App.tsx'

// Dark "mission center" theme — keep the near-black palette the console already uses.
const theme = createTheme({
  primaryColor: 'indigo',
  fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif',
  defaultRadius: 'md',
})

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <MantineProvider theme={theme} defaultColorScheme="dark" forceColorScheme="dark">
      <App />
    </MantineProvider>
  </React.StrictMode>,
)
