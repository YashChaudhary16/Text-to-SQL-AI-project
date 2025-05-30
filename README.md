# Text-to-SQL AI Agent

A powerful AI-powered application that converts natural language questions into SQL queries using Claude 3.5 Haiku. This tool allows users to interact with SQLite databases using plain English, making database querying accessible to non-technical users.

## 🌟 Features

- **Natural Language to SQL**: Convert plain English questions into SQL queries
- **Database Schema Support**: Works with both SQLite databases and SQL schema files
- **Interactive UI**: User-friendly Streamlit interface
- **Real-time Query Execution**: Execute generated SQL queries and view results
- **Schema Visualization**: View database structure before querying
- **Error Handling**: Clear error messages and explanations

## 🚀 Getting Started

### Prerequisites

- Python 3.8 or higher
- pip (Python package installer)

### Installation

1. Clone the repository:
```bash
git clone https://github.com/YashChaudhary16/Text-to-SQL-AI-Agent.git
cd Text-to-SQL-AI-Agent
```

2. Create a virtual environment (recommended):
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install dependencies:
```bash
pip install -r requirements.txt
```

4. Create a `.env` file in the project root and add your Anthropic API key:
```
ANTHROPIC_API_KEY=your_api_key_here
```

### Running the Application

1. Start the Streamlit app:
```bash
streamlit run app.py
```

2. Open your web browser and navigate to the URL shown in the terminal (typically http://localhost:8501)

## 💡 Usage Guide

1. **Upload Database**:
   - Click the file uploader to select your SQLite database (.sqlite, .db) or SQL schema file (.sql, .txt)
   - Maximum file size: 200MB

2. **View Schema**:
   - After uploading, the database schema will be displayed
   - Review the tables and their relationships

3. **Ask Questions**:
   - Type your question in natural language
   - Example: "List all customer names and emails"
   - The AI will generate and execute the appropriate SQL query

4. **View Results**:
   - Generated SQL query will be displayed
   - Query results will be shown in a table format
   - Any errors will be clearly displayed with explanations

## 🔧 Supported File Types

- SQLite databases (.sqlite, .db)
- SQL schema files (.sql, .txt)

## ⚠️ Important Notes

- Keep your `.env` file secure and never commit it to version control
- The application creates temporary database files for processing
- Large databases may take longer to process
- Ensure your questions are clear and specific for better results

## 🤝 Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## 📝 License

This project is licensed under the MIT License - see the LICENSE file for details.

## 🙏 Acknowledgments

- Built with [Streamlit](https://streamlit.io/)
- Powered by [Claude 3.5 Haiku](https://www.anthropic.com/claude)
- Uses [SQLite](https://www.sqlite.org/) for database management 