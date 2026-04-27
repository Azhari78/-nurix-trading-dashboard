import { FaSearch } from 'react-icons/fa';

export default function Header({ searchTerm, setSearchTerm }) {
  return (
    <header className="bg-gray-800 shadow-md p-4">
      <div className="container mx-auto flex flex-col sm:flex-row justify-between items-center">
        <h1 className="text-2xl font-bold text-white mb-2 sm:mb-0">
          Crypto Trading Dashboard
        </h1>
        <div className="relative">
          <input
            type="text"
            placeholder="Search coin..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            className="bg-gray-700 text-white rounded-lg px-4 py-2 pl-10 focus:outline-none focus:ring-2 focus:ring-blue-500 w-64"
          />
          <FaSearch className="absolute left-3 top-3 text-gray-400" />
        </div>
      </div>
    </header>
  );
}
