/**
 * Invisible component that keeps track of the current virtual URL and fires off Google Analytics
 * events when it changes.
 */

import { Component } from 'react';
import { connect } from 'react-redux';
import { push } from 'react-router-redux';

const symbolPageRegex = /\/symbol\/(.+)/;

/**
 * If we're currently on a symbol details page, check to see if the symbol is lowercase.  If it
 * is, push a new path where the symbol is capitalized instead.
 */
const checkSymbolPage = path => {
  const match = symbolPageRegex.exec(path);
  if (!match) {
    return false;
  }

  const uppercaseSymbol = match[1].toUpperCase();
  if (uppercaseSymbol !== match[1]) {
    return uppercaseSymbol;
  }
};

const sendGa = curPath => {
  window.ga('set', 'page', curPath);
  window.ga('send', 'pageview');
};

class PageTracker extends Component {
  checkLowercaseSymbol = () => {
    const { path: curPath, push } = this.props;

    const newPath = checkSymbolPage(curPath);
    if (newPath) {
      push(newPath);
      return true;
    } else {
      return false;
    }
  };

  componentDidMount = this.checkLowercaseSymbol;

  componentDidUpdate = prevProps => {
    const curPath = this.props.path;
    if (this.checkLowercaseSymbol()) {
      return;
    }

    if (prevProps.path !== this.props.path) {
      window.ga && sendGa(curPath);
    }
  };

  render = () => null;
}

const mapStateToProps = ({
  router: {
    location: { pathname },
  },
}) => ({ path: pathname });

export default connect(
  mapStateToProps,
  { push }
)(PageTracker);
